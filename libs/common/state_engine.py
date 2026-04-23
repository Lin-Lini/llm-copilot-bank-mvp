from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    CompromiseSignal,
    DisputeSubtype,
    Intent,
    Phase,
    Plan,
    PlanStep,
    RequestedAction,
    StatusContext,
    ToolName,
    ToolUI,
)


_TXN_REQUIRED_FIELDS = {'card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'}
_RECURRING_EXTRA_FIELDS = {'merchant_name_confirm'}
_BLOCK_CONFIRM_FIELD = 'customer_confirm_block'
_STATUS_REQUIRED_FIELDS = {'case_id'}


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


def _requested_actions(analyze: AnalyzeV1 | None) -> set[RequestedAction]:
    if not analyze:
        return set()
    return set(analyze.facts.requested_actions or [])


def _compromise_signals(analyze: AnalyzeV1 | None) -> set[CompromiseSignal]:
    if not analyze:
        return set()
    return set(analyze.facts.compromise_signals or [])


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

    if intent == Intent.UnblockReissue:
        return Plan(
            current_step_id='collect_resolution',
            steps=[
                PlanStep(id='collect_resolution', title='Уточнение запроса на разблокировку или перевыпуск', done=False),
                PlanStep(id='verify_resolution_path', title='Проверка допустимого сценария', done=False),
                PlanStep(id='execute_resolution', title='Выполнение подтверждённого действия', done=False),
                PlanStep(id='explain_resolution', title='Пояснение дальнейших шагов', done=False),
            ],
        )

    if intent == Intent.CardNotWorking:
        return Plan(
            current_step_id='identify_channel',
            steps=[
                PlanStep(id='identify_channel', title='Уточнение канала и характера проблемы', done=False),
                PlanStep(id='check_limits_settings', title='Проверка ограничений и настроек', done=False),
                PlanStep(id='decide_resolution', title='Выбор действия по результату проверки', done=False),
                PlanStep(id='explain_resolution', title='Пояснение дальнейших шагов', done=False),
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
        enabled = phase in {Phase.Act, Phase.Explain}
        return [
            _tool(
                ToolName.get_case_status,
                'Проверить статус обращения',
                enabled,
                'Клиенту нужен подтверждённый статус и следующий шаг.' if enabled else 'Нужен номер обращения или подтвержденный контекст кейса.',
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
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.'),
        ]

    if intent == Intent.UnblockReissue:
        if phase == Phase.Collect:
            return [
                _tool(ToolName.unblock_card, 'Разблокировать карту (mock)', False, 'Сначала нужно определить, допустима ли разблокировка.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', False, 'Сначала нужно подтвердить необходимость перевыпуска.'),
                _tool(ToolName.get_case_status, 'Проверить статус обращения', False, 'Если запрос связан с уже существующим кейсом, нужен его номер.'),
            ]
        if phase == Phase.Act:
            return [
                _tool(ToolName.unblock_card, 'Разблокировать карту (mock)', True, 'Можно выполнить разблокировку в допустимом сценарии.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', True, 'Можно оформить перевыпуск после подтверждения сценария.'),
                _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'При необходимости можно уточнить связанный статус обращения.'),
            ]
        return [
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен подтверждённый статус и следующий шаг.'),
        ]

    if intent == Intent.CardNotWorking:
        if phase == Phase.Collect:
            return [
                _tool(ToolName.get_card_limits, 'Проверить лимиты и настройки (mock)', False, 'Сначала нужно уточнить, где именно не работает карта.'),
                _tool(ToolName.toggle_online_payments, 'Включить или выключить онлайн-платежи (mock)', False, 'Нужно подтвердить, что проблема связана с онлайн-оплатой.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', False, 'Перевыпуск возможен после подтверждения повреждения карты.'),
            ]
        if phase == Phase.Act:
            return [
                _tool(ToolName.get_card_limits, 'Проверить лимиты и настройки (mock)', True, 'Можно проверить ограничения и настройки карты.'),
                _tool(ToolName.toggle_online_payments, 'Включить или выключить онлайн-платежи (mock)', True, 'Можно изменить настройки онлайн-платежей после подтверждения сценария.'),
                _tool(ToolName.reissue_card, 'Перевыпуск карты (mock)', False, 'Перевыпуск доступен только при признаках повреждения карты.'),
            ]
        return [
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Если по проблеме уже создано обращение, можно уточнить статус.'),
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
            _tool(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.'),
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
        if _has_step(plan, 'verify_resolution_path'):
            return _apply_done(plan, {'collect_resolution', 'verify_resolution_path'}, fallback_step='execute_resolution')
        if _has_step(plan, 'check_limits_settings'):
            return _apply_done(plan, {'identify_channel'}, fallback_step='check_limits_settings')
        return _apply_done(plan, {'collect'}, fallback_step='act')

    if _has_step(plan, 'explain_next'):
        return _apply_done(plan, {'collect_core', 'risk_check', 'act_get_txn', 'case_create'}, fallback_step='explain_next')
    if _has_step(plan, 'explain_reissue'):
        return _apply_done(plan, {'collect_risk', 'block_now', 'case_or_escalate'}, fallback_step='explain_reissue')
    if _has_step(plan, 'next_step'):
        return _apply_done(plan, {'identify_case', 'status_check'}, fallback_step='next_step')
    if _has_step(plan, 'explain_resolution'):
        return _apply_done(
            plan,
            {step.id for step in plan.steps if step.id != 'explain_resolution'},
            fallback_step='explain_resolution',
        )
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

    if tool in {ToolName.unblock_card, ToolName.reissue_card, ToolName.get_card_limits, ToolName.set_card_limits, ToolName.toggle_online_payments}:
        if _has_step(plan, 'case_or_escalate'):
            return _apply_done(plan, {'case_or_escalate'}, fallback_step='explain_reissue')
        if _has_step(plan, 'execute_resolution'):
            return _apply_done(plan, {'execute_resolution'}, fallback_step='explain_resolution')
        if _has_step(plan, 'check_limits_settings'):
            return _apply_done(plan, {'check_limits_settings'}, fallback_step='decide_resolution')
        if _has_step(plan, 'decide_resolution'):
            return _apply_done(plan, {'decide_resolution'}, fallback_step='explain_resolution')
        return _apply_done(plan, {'act'}, fallback_step='explain')

    return plan


def phase_from_plan(plan: Plan) -> Phase:
    current = plan.current_step_id
    if current in {'explain', 'explain_next', 'explain_reissue', 'next_step', 'explain_resolution'}:
        return Phase.Explain
    if current in {'act', 'act_get_txn', 'case_create', 'block_now', 'case_or_escalate', 'status_check', 'execute_resolution', 'check_limits_settings', 'decide_resolution'}:
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
    analyze: AnalyzeV1 | None = None,
) -> list[ToolUI]:
    missing = set(missing_fields or [])
    confirmed = set(confirmed_fields or [])
    effective_missing = missing - confirmed
    params = execution_params or {}
    requested_actions = _requested_actions(analyze)
    compromise_signals = _compromise_signals(analyze)
    dispute_subtype = analyze.facts.dispute_subtype if analyze else DisputeSubtype.unknown
    card_state = analyze.facts.card_state if analyze else CardState.unknown
    status_context = analyze.facts.status_context if analyze else StatusContext.unknown

    resolved: list[ToolUI] = []

    for tool_ui in allowed_tools(intent, phase):
        current = tool_ui

        if current.tool == ToolName.get_transactions:
            required = set(_TXN_REQUIRED_FIELDS)
            if dispute_subtype == DisputeSubtype.recurring_subscription:
                required |= _RECURRING_EXTRA_FIELDS
            if required & effective_missing:
                reason = 'Нужно уточнить наличие карты, сумму и время операции.'
                if dispute_subtype == DisputeSubtype.recurring_subscription:
                    reason = 'Нужно уточнить сумму, время и название сервиса или подписки.'
                elif dispute_subtype == DisputeSubtype.duplicate_charge:
                    reason = 'Нужно подтвердить сумму, время и факт двойного списания.'
                elif dispute_subtype == DisputeSubtype.reversal_pending:
                    reason = 'Нужно уточнить время операции и подтвердить, что речь идет о холде или резерве.'
                current = current.model_copy(update={'enabled': False, 'reason': reason})
            elif dispute_subtype == DisputeSubtype.recurring_subscription:
                current = current.model_copy(update={'enabled': True, 'reason': 'Можно проверить спорное регулярное списание или подписку.'})
            elif dispute_subtype == DisputeSubtype.duplicate_charge:
                current = current.model_copy(update={'enabled': True, 'reason': 'Можно сравнить операции и проверить возможное двойное списание.'})
            elif dispute_subtype == DisputeSubtype.reversal_pending:
                current = current.model_copy(update={'enabled': True, 'reason': 'Можно проверить статус холда или незавершенного списания.'})

        if current.tool == ToolName.block_card:
            requested_confirm = bool(params.get('client_confirmed'))
            confirmed_from_state = _BLOCK_CONFIRM_FIELD in confirmed
            high_risk_intent = intent in {Intent.BlockCard, Intent.LostStolen}
            high_risk_signals = bool(compromise_signals)

            if dispute_subtype in {DisputeSubtype.recurring_subscription, DisputeSubtype.duplicate_charge, DisputeSubtype.reversal_pending} and not (RequestedAction.block_card in requested_actions or requested_confirm or confirmed_from_state):
                current = current.model_copy(update={'enabled': False, 'reason': 'По текущему подтипу спора блокировка не является первоочередным действием без отдельного подтверждения клиента.'})
            elif requested_confirm or confirmed_from_state or high_risk_intent or high_risk_signals:
                reason = 'Подтверждение клиента получено.'
                if high_risk_intent and not (requested_confirm or confirmed_from_state):
                    reason = 'Сценарий повышенного риска допускает блокировку.'
                if high_risk_signals and not (requested_confirm or confirmed_from_state or high_risk_intent):
                    reason = 'Есть признаки компрометации, допускающие приоритетную блокировку.'
                current = current.model_copy(update={'enabled': True, 'reason': reason})
            else:
                current = current.model_copy(update={'enabled': False, 'reason': 'Нужно явное подтверждение клиента на блокировку.'})

        if current.tool == ToolName.reissue_card:
            if intent in {Intent.LostStolen, Intent.UnblockReissue} and card_state in {CardState.lost, CardState.stolen, CardState.damaged}:
                current = current.model_copy(update={'enabled': True, 'reason': 'Сценарий допускает перевыпуск после подтверждения проблемы с картой.'})
            elif intent == Intent.CardNotWorking and card_state == CardState.damaged:
                current = current.model_copy(update={'enabled': True, 'reason': 'Карта выглядит поврежденной, можно предложить перевыпуск.'})
            elif intent == Intent.CardNotWorking:
                current = current.model_copy(update={'enabled': False, 'reason': 'Перевыпуск нужен только при подтвержденном повреждении карты.'})

        if current.tool == ToolName.unblock_card:
            if intent == Intent.UnblockReissue and RequestedAction.unblock_card in requested_actions and status_context in {StatusContext.case_known, StatusContext.waiting_review, StatusContext.resolved, StatusContext.unknown}:
                if compromise_signals or card_state in {CardState.lost, CardState.stolen}:
                    current = current.model_copy(update={'enabled': False, 'reason': 'Разблокировка недопустима при признаках компрометации или утраты карты.'})
                elif 'case_id' in effective_missing:
                    current = current.model_copy(update={'enabled': False, 'reason': 'Для разблокировки нужен номер обращения или подтвержденный контекст блокировки.'})
                else:
                    current = current.model_copy(update={'enabled': True, 'reason': 'Запрос на разблокировку подтвержден и не противоречит текущему рисковому контексту.'})
            elif intent == Intent.UnblockReissue:
                current = current.model_copy(update={'enabled': False, 'reason': 'Сначала нужно подтвердить, что клиент просит именно разблокировку.'})

        if current.tool == ToolName.get_case_status:
            if status_context in {StatusContext.case_known, StatusContext.waiting_review, StatusContext.resolved} and not (_STATUS_REQUIRED_FIELDS & effective_missing):
                current = current.model_copy(update={'enabled': True, 'reason': 'Есть контекст существующего обращения, можно запросить статус.'})
            elif intent == Intent.StatusWhatNext:
                current = current.model_copy(update={'enabled': False, 'reason': 'Нужен номер обращения или подтвержденный контекст кейса.'})

        if current.tool == ToolName.get_card_limits:
            if intent == Intent.CardNotWorking:
                if 'problem_channel_confirm' in effective_missing:
                    current = current.model_copy(update={'enabled': False, 'reason': 'Сначала нужно уточнить, где именно не работает карта.'})
                else:
                    current = current.model_copy(update={'enabled': True, 'reason': 'Можно проверить лимиты и ограничения карты.'})

        if current.tool == ToolName.toggle_online_payments:
            if intent == Intent.CardNotWorking:
                if 'problem_channel_confirm' in effective_missing:
                    current = current.model_copy(update={'enabled': False, 'reason': 'Сначала нужно подтвердить, что проблема связана с онлайн-оплатой.'})
                elif analyze and analyze.facts.channel_hint != 'online':
                    current = current.model_copy(update={'enabled': False, 'reason': 'Из контекста не следует, что проблема связана именно с онлайн-оплатой.'})
                else:
                    current = current.model_copy(update={'enabled': True, 'reason': 'Можно изменить настройки онлайн-платежей после подтверждения сценария.'})

        if safe_mode != 'ok' and current.tool not in {ToolName.create_case, ToolName.get_case_status}:
            current = current.model_copy(update={'enabled': False, 'reason': 'В safe mode доступны только безопасные действия.'})

        resolved.append(current)

    return resolved