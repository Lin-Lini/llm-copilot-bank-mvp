from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from contracts.schemas import AnalyzeV1, Intent, Phase, Plan, PlanStep, ToolName, ToolUI


_TXN_REQUIRED_FIELDS = {'card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'}
_BLOCK_CONFIRM_FIELD = 'customer_confirm_block'


def _tool(tool: ToolName, label: str, enabled: bool, reason: str) -> ToolUI:
    return ToolUI(tool=tool, label=label, enabled=enabled, reason=reason)


def _has_step(plan: Plan, step_id: str) -> bool:
    return any(step.id == step_id for step in plan.steps)


def _apply_done(plan: Plan, done_ids: set[str], *, fallback_step: str | None = None) -> Plan:
    steps = [
        step.model_copy(update={'done': step.done or step.id in done_ids})
        for step in plan.steps
    ]

    current_step_id = next((step.id for step in steps if not step.done), steps[-1].id if steps else '')
    if fallback_step and any(step.id == fallback_step for step in steps):
        current_step_id = fallback_step

    return plan.model_copy(update={'steps': steps, 'current_step_id': current_step_id})


def build_plan(intent: Intent) -> Plan:
    if intent == Intent.SuspiciousTransaction:
        return Plan(
            current_step_id='collect_core',
            steps=[
                PlanStep(id='collect_core', title='Сбор обязательных данных', done=False),
                PlanStep(id='risk_check', title='Проверка риска мошенничества', done=False),
                PlanStep(id='act_get_txn', title='Сверка операций (инструмент)', done=False),
                PlanStep(id='case_create', title='Создание обращения', done=False),
                PlanStep(id='explain_next', title='Пояснение дальнейших шагов', done=False),
            ],
        )

    if intent in {Intent.BlockCard, Intent.LostStolen}:
        return Plan(
            current_step_id='collect_risk',
            steps=[
                PlanStep(id='collect_risk', title='Подтверждение ситуации и уровня риска', done=False),
                PlanStep(id='block_now', title='Блокировка карты', done=False),
                PlanStep(id='case_or_escalate', title='Фиксация кейса / эскалация', done=False),
                PlanStep(id='explain_reissue', title='Пояснение дальнейших шагов', done=False),
            ],
        )

    if intent == Intent.StatusWhatNext:
        return Plan(
            current_step_id='identify_case',
            steps=[
                PlanStep(id='identify_case', title='Уточнение номера и контекста обращения', done=False),
                PlanStep(id='status_check', title='Проверка статуса', done=False),
                PlanStep(id='next_step', title='Пояснение следующего шага', done=False),
            ],
        )

    return Plan(
        current_step_id='collect',
        steps=[
            PlanStep(id='collect', title='Сбор данных', done=False),
            PlanStep(id='act', title='Действие (инструмент)', done=False),
            PlanStep(id='explain', title='Пояснение и дальнейшие шаги', done=False),
        ],
    )


def allowed_tools(intent: Intent, phase: Phase) -> list[ToolUI]:
    if intent == Intent.StatusWhatNext:
        if phase == Phase.Explain:
            return [
                _tool(
                    ToolName.get_case_status,
                    'Проверить статус обращения',
                    True,
                    'Клиенту нужен подтверждённый статус и следующий шаг.',
                )
            ]
        return [
            _tool(
                ToolName.get_case_status,
                'Проверить статус обращения',
                False,
                'Нужен номер обращения или подтвержденный контекст кейса.',
            )
        ]

    if intent in {Intent.BlockCard, Intent.LostStolen}:
        if phase == Phase.Collect:
            return [
                _tool(ToolName.block_card, 'Заблокировать карту (mock)', False, 'Нужно подтверждение клиента или сценарий повышенного риска.'),
                _tool(ToolName.create_case, 'Создать обращение', True, 'Можно зафиксировать обращение и детали риска.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', False, 'Сначала нужна блокировка или подтверждение сценария.'),
            ]
        if phase == Phase.Act:
            return [
                _tool(ToolName.block_card, 'Заблокировать карту (mock)', True, 'Подтверждение на блокировку получено.'),
                _tool(ToolName.create_case, 'Создать обращение', True, 'Нужно зафиксировать кейс и риск.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', True, 'После блокировки можно предложить перевыпуск.'),
            ]
        return [
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.')
        ]

    if phase == Phase.Collect:
        return [
            _tool(ToolName.create_case, 'Создать обращение', True, 'Можно зарегистрировать обращение с последующим уточнением.'),
            _tool(ToolName.get_transactions, 'Открыть операции (mock)', False, 'Нужно уточнить параметры операции.'),
            _tool(ToolName.block_card, 'Заблокировать карту (mock)', False, 'Нужно подтверждение клиента.'),
        ]

    if phase == Phase.Act:
        return [
            _tool(ToolName.get_transactions, 'Открыть операции (mock)', True, 'Данных достаточно для сверки.'),
            _tool(ToolName.block_card, 'Заблокировать карту (mock)', True, 'Подтверждено клиентом или сценарий высокорисковый.'),
            _tool(ToolName.create_case, 'Создать обращение', True, 'Оформление обращения.'),
        ]

    if phase == Phase.Explain:
        return [
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.')
        ]

    return []


def reduce_plan_after_analyze(plan: Plan, an: AnalyzeV1) -> Plan:
    if an.phase == Phase.Collect:
        return _apply_done(plan, set())

    if an.phase == Phase.Act:
        if _has_step(plan, 'act_get_txn'):
            return _apply_done(plan, {'collect_core', 'risk_check'}, fallback_step='act_get_txn')
        if _has_step(plan, 'block_now'):
            return _apply_done(plan, {'collect_risk'}, fallback_step='block_now')
        if _has_step(plan, 'status_check'):
            return _apply_done(plan, {'identify_case'}, fallback_step='status_check')
        return _apply_done(plan, {'collect'}, fallback_step='act')

    if _has_step(plan, 'explain_next'):
        return _apply_done(plan, {'collect_core', 'risk_check', 'act_get_txn', 'case_create'}, fallback_step='explain_next')
    if _has_step(plan, 'explain_reissue'):
        return _apply_done(plan, {'collect_risk', 'block_now', 'case_or_escalate'}, fallback_step='explain_reissue')
    if _has_step(plan, 'next_step'):
        return _apply_done(plan, {'identify_case', 'status_check'}, fallback_step='next_step')
    return _apply_done(plan, {'collect', 'act'}, fallback_step='explain')


def reduce_plan_after_tool(plan: Plan, tool_name: str) -> Plan:
    tool = ToolName(tool_name)

    if tool == ToolName.get_transactions:
        if _has_step(plan, 'act_get_txn'):
            done_ids = {'act_get_txn'}
            next_step = 'case_create' if _has_step(plan, 'case_create') else 'explain_next'
            return _apply_done(plan, done_ids, fallback_step=next_step)
        return _apply_done(plan, {'act'}, fallback_step='explain')

    if tool == ToolName.create_case:
        if _has_step(plan, 'case_create'):
            act_get_txn_pending = any(step.id == 'act_get_txn' and not step.done for step in plan.steps)
            if act_get_txn_pending:
                return _apply_done(plan, {'case_create'}, fallback_step='act_get_txn')
            return _apply_done(plan, {'case_create'}, fallback_step='explain_next')

        if _has_step(plan, 'case_or_escalate'):
            return _apply_done(plan, {'case_or_escalate'}, fallback_step='explain_reissue')

        return _apply_done(plan, {'act'}, fallback_step='explain')

    if tool == ToolName.get_case_status:
        if _has_step(plan, 'status_check'):
            return _apply_done(plan, {'status_check'}, fallback_step='next_step')
        return _apply_done(plan, set(), fallback_step='explain')

    if tool == ToolName.block_card:
        if _has_step(plan, 'block_now'):
            next_step = 'case_or_escalate' if _has_step(plan, 'case_or_escalate') else 'explain_reissue'
            return _apply_done(plan, {'block_now'}, fallback_step=next_step)
        return _apply_done(plan, {'act'}, fallback_step='explain')

    if tool in {
        ToolName.unblock_card,
        ToolName.reissue_card,
        ToolName.get_card_limits,
        ToolName.set_card_limits,
        ToolName.toggle_online_payments,
    }:
        if _has_step(plan, 'case_or_escalate'):
            return _apply_done(plan, {'case_or_escalate'}, fallback_step='explain_reissue')
        return _apply_done(plan, {'act'}, fallback_step='explain')

    return plan

def phase_from_plan(plan: Plan) -> Phase:
    current = plan.current_step_id
    if current in {'explain', 'explain_next', 'explain_reissue', 'next_step'}:
        return Phase.Explain
    if current in {'act', 'act_get_txn', 'case_create', 'block_now', 'case_or_escalate', 'status_check'}:
        return Phase.Act
    return Phase.Collect


def resolve_tools(
    intent: Intent,
    phase: Phase,
    *,
    missing_fields: Iterable[str] | None = None,
    confirmed_fields: Iterable[str] | None = None,
    safe_mode: str = 'ok',
    execution_params: dict[str, Any] | None = None,
) -> list[ToolUI]:
    missing = set(missing_fields or [])
    confirmed = set(confirmed_fields or [])
    effective_missing = missing - confirmed
    params = execution_params or {}

    resolved: list[ToolUI] = []
    for tool_ui in allowed_tools(intent, phase):
        current = tool_ui

        if current.tool == ToolName.get_transactions and (_TXN_REQUIRED_FIELDS & effective_missing):
            current = current.model_copy(
                update={
                    'enabled': False,
                    'reason': 'Нужно уточнить наличие карты, сумму и время операции.',
                }
            )

        if current.tool == ToolName.block_card:
            requested_confirm = bool(params.get('client_confirmed'))
            confirmed_from_state = _BLOCK_CONFIRM_FIELD in confirmed
            high_risk_intent = intent in {Intent.BlockCard, Intent.LostStolen}

            if requested_confirm or confirmed_from_state or high_risk_intent:
                reason = 'Подтверждение клиента получено.'
                if high_risk_intent and not (requested_confirm or confirmed_from_state):
                    reason = 'Сценарий повышенного риска допускает блокировку.'
                current = current.model_copy(update={'enabled': True, 'reason': reason})
            else:
                current = current.model_copy(
                    update={
                        'enabled': False,
                        'reason': 'Нужно явное подтверждение клиента на блокировку.',
                    }
                )

        if safe_mode != 'ok' and current.tool not in {ToolName.create_case}:
            current = current.model_copy(
                update={
                    'enabled': False,
                    'reason': 'В safe mode доступны только безопасные действия.',
                }
            )

        resolved.append(current)

    return resolved