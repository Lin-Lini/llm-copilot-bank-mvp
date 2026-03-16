from __future__ import annotations

from contracts.schemas import Intent, Phase, ToolName, Plan, PlanStep, ToolUI


def build_plan(intent: Intent) -> Plan:
    # детерминированный план по intent (не UI двигает шаги)
    if intent == Intent.SuspiciousTransaction:
        steps = [
            PlanStep(id='collect_core', title='Сбор обязательных данных', done=False),
            PlanStep(id='risk_check', title='Проверка риска мошенничества', done=False),
            PlanStep(id='act_get_txn', title='Сверка операций (инструмент)', done=False),
            PlanStep(id='case_create', title='Создание обращения', done=False),
            PlanStep(id='explain_next', title='Пояснение дальнейших шагов', done=False),
        ]
        return Plan(current_step_id='collect_core', steps=steps)

    steps = [
        PlanStep(id='collect', title='Сбор данных', done=False),
        PlanStep(id='act', title='Действие (инструмент)', done=False),
        PlanStep(id='explain', title='Пояснение и дальнейшие шаги', done=False),
    ]
    return Plan(current_step_id='collect', steps=steps)


def allowed_tools(intent: Intent, phase: Phase) -> list[ToolUI]:
    # allowlist по intent/phase
    base = []

    def t(tool: ToolName, label: str, enabled: bool, reason: str) -> ToolUI:
        return ToolUI(tool=tool, label=label, enabled=enabled, reason=reason)

    if phase == Phase.Collect:
        base.append(t(ToolName.create_case, 'Создать обращение', True, 'Можно зарегистрировать обращение с последующим уточнением.'))
        base.append(t(ToolName.get_transactions, 'Открыть операции (mock)', False, 'Нужно уточнить параметры операции.'))
        base.append(t(ToolName.block_card, 'Заблокировать карту (mock)', False, 'Нужно подтверждение клиента.'))
        return base

    if phase == Phase.Act:
        base.append(t(ToolName.get_transactions, 'Открыть операции (mock)', True, 'Данных достаточно для сверки.'))
        base.append(t(ToolName.block_card, 'Заблокировать карту (mock)', True, 'Подтверждено клиентом.'))
        base.append(t(ToolName.create_case, 'Создать обращение', True, 'Оформление обращения.'))
        return base

    if phase == Phase.Explain:
        base.append(t(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.'))
        return base

    return base
