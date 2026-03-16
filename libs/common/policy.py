from __future__ import annotations

from contracts.schemas import Intent, Phase, ToolName, Plan, PlanStep, ToolUI


def build_plan(intent: Intent) -> Plan:
    if intent == Intent.SuspiciousTransaction:
        steps = [
            PlanStep(id='collect_core', title='Сбор обязательных данных', done=False),
            PlanStep(id='risk_check', title='Проверка риска мошенничества', done=False),
            PlanStep(id='act_get_txn', title='Сверка операций (инструмент)', done=False),
            PlanStep(id='case_create', title='Создание обращения', done=False),
            PlanStep(id='explain_next', title='Пояснение дальнейших шагов', done=False),
        ]
        return Plan(current_step_id='collect_core', steps=steps)

    if intent in {Intent.BlockCard, Intent.LostStolen}:
        steps = [
            PlanStep(id='collect_risk', title='Подтверждение ситуации и уровня риска', done=False),
            PlanStep(id='block_now', title='Блокировка карты', done=False),
            PlanStep(id='case_or_escalate', title='Фиксация кейса / эскалация', done=False),
            PlanStep(id='explain_reissue', title='Пояснение дальнейших шагов', done=False),
        ]
        return Plan(current_step_id='collect_risk', steps=steps)

    if intent == Intent.StatusWhatNext:
        steps = [
            PlanStep(id='identify_case', title='Уточнение номера и контекста обращения', done=False),
            PlanStep(id='status_check', title='Проверка статуса', done=False),
            PlanStep(id='next_step', title='Пояснение следующего шага', done=False),
        ]
        return Plan(current_step_id='identify_case', steps=steps)

    steps = [
        PlanStep(id='collect', title='Сбор данных', done=False),
        PlanStep(id='act', title='Действие (инструмент)', done=False),
        PlanStep(id='explain', title='Пояснение и дальнейшие шаги', done=False),
    ]
    return Plan(current_step_id='collect', steps=steps)


def allowed_tools(intent: Intent, phase: Phase) -> list[ToolUI]:
    base = []

    def t(tool: ToolName, label: str, enabled: bool, reason: str) -> ToolUI:
        return ToolUI(tool=tool, label=label, enabled=enabled, reason=reason)

    if intent == Intent.StatusWhatNext:
        if phase == Phase.Explain:
            return [t(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен подтверждённый статус и следующий шаг.')]
        return [t(ToolName.get_case_status, 'Проверить статус обращения', False, 'Нужен номер обращения или подтвержденный контекст кейса.')]

    if intent in {Intent.BlockCard, Intent.LostStolen}:
        if phase == Phase.Collect:
            return [
                t(ToolName.block_card, 'Заблокировать карту (mock)', False, 'Нужно подтверждение клиента или сценарий повышенного риска.'),
                t(ToolName.create_case, 'Создать обращение', True, 'Можно зафиксировать обращение и детали риска.'),
                t(ToolName.reissue_card, 'Перевыпуск карты (mock)', False, 'Сначала нужна блокировка или подтверждение сценария.'),
            ]
        if phase == Phase.Act:
            return [
                t(ToolName.block_card, 'Заблокировать карту (mock)', True, 'Подтверждение на блокировку получено.'),
                t(ToolName.create_case, 'Создать обращение', True, 'Нужно зафиксировать кейс и риск.'),
                t(ToolName.reissue_card, 'Перевыпуск карты (mock)', True, 'После блокировки можно предложить перевыпуск.'),
            ]
        return [t(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.')]

    if phase == Phase.Collect:
        base.append(t(ToolName.create_case, 'Создать обращение', True, 'Можно зарегистрировать обращение с последующим уточнением.'))
        base.append(t(ToolName.get_transactions, 'Открыть операции (mock)', False, 'Нужно уточнить параметры операции.'))
        base.append(t(ToolName.block_card, 'Заблокировать карту (mock)', False, 'Нужно подтверждение клиента.'))
        return base

    if phase == Phase.Act:
        base.append(t(ToolName.get_transactions, 'Открыть операции (mock)', True, 'Данных достаточно для сверки.'))
        base.append(t(ToolName.block_card, 'Заблокировать карту (mock)', True, 'Подтверждено клиентом или сценарий высокорисковый.'))
        base.append(t(ToolName.create_case, 'Создать обращение', True, 'Оформление обращения.'))
        return base

    if phase == Phase.Explain:
        base.append(t(ToolName.get_case_status, 'Проверить статус обращения', True, 'Клиенту нужен статус и следующий шаг.'))
        return base

    return base
