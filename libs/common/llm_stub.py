from __future__ import annotations

import re
from typing import Any

from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    ChannelHint,
    DangerFlag,
    DraftV1,
    ExplainUpdates,
    ExplainV1,
    FactsPreview,
    FormCard,
    FormField,
    Intent,
    Phase,
    Plan,
    ProfileUpdate,
    QuickCard,
    QuickCardKind,
    RiskChecklistItem,
    RiskLevel,
    Severity,
    Sidebar,
    SourceOut,
    ToolSuggested,
    ToolUI,
)


def _risk_checklist() -> list[RiskChecklistItem]:
    return [
        RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.'),
        RiskChecklistItem(id='no_pin', severity=Severity.high, text='Не запрашивать ПИН-код.'),
        RiskChecklistItem(id='no_sms_codes', severity=Severity.high, text='Не запрашивать одноразовые коды из SMS/Push.'),
        RiskChecklistItem(id='no_full_pan', severity=Severity.high, text='Не запрашивать полный номер карты.'),
        RiskChecklistItem(id='no_refund_promise', severity=Severity.medium, text='Не обещать возврат средств; исход зависит от рассмотрения.'),
        RiskChecklistItem(id='anti_remote_access', severity=Severity.high, text='Не рекомендовать удаленный доступ/установку приложений.'),
    ]


def analyze(history_redacted: str) -> AnalyzeV1:
    text = history_redacted.lower()

    def has(*parts: str) -> bool:
        return any(p in text for p in parts)

    intent = Intent.Unknown
    if has('статус обращения', 'что со статусом', 'номер обращения', 'когда рассмотрят', 'что дальше по обращению'):
        intent = Intent.StatusWhatNext
    elif has('потерял', 'потеряла', 'украли карту', 'карта пропала', 'утеря карты', 'кража карты'):
        intent = Intent.LostStolen
    elif has('заблокируйте карту', 'заблокировать карту', 'блокировка карты'):
        intent = Intent.BlockCard
    elif has('не работает карта', 'карта не работает', 'не проходит оплата'):
        intent = Intent.CardNotWorking
    elif has('перевыпуск', 'разблокировать карту', 'разблокировка'):
        intent = Intent.UnblockReissue
    elif has('списан', 'операц', 'не совершал', 'не я', 'мошен', 'подписк', 'дважды', 'двойное списание', 'дубликат'):
        intent = Intent.SuspiciousTransaction

    phase = Phase.Collect
    missing: list[str] = []
    summary = 'Клиент обратился с вопросом по карте.'
    risk_level = RiskLevel.low
    recurring: list[str] = []
    notes = ''
    next_questions: list[str] = []
    tools: list[ToolSuggested] = []

    has_card = bool(re.search(r'карта\s+(у\s+меня|на\s+руках|со\s+мной|при\s+мне)', text))
    has_amount = bool(re.search(r'\b\d{2,7}\b', text))
    has_time = bool(re.search(r'(\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b|вчера|сегодня|утром|вечером)', text))
    wants_block = bool(re.search(r'(заблокир(уйте|овать)|блокир(уйте|овать))', text))
    has_case_id = bool(re.search(r'case[-_ ]?\d+|обращени', text))

    if intent == Intent.SuspiciousTransaction:
        summary = 'Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.'
        risk_level = RiskLevel.high
        recurring = ['suspicious_transaction']
        notes = 'Уточнить детали операции, факт владения картой и необходимость блокировки.'
        missing = ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block']
        if has_card and has_amount and has_time:
            missing = []
            phase = Phase.Act
        elif has_card and has_amount:
            missing = ['txn_datetime_confirm', 'customer_confirm_block']
        if wants_block and 'customer_confirm_block' in missing:
            missing.remove('customer_confirm_block')
        next_questions = [
            'Подтвердите, пожалуйста, что карта сейчас находится у вас (да/нет).',
            'Подтвердите сумму и примерное время операции, чтобы сверить данные.',
            'Подтвердите, что вы не совершали эту операцию и хотите зафиксировать обращение.',
        ]
        tools = [
            ToolSuggested(tool='get_transactions', reason='Нужно сверить детали спорной операции по списку транзакций.', params_hint={'date_range': 'последние 7 дней'}),
            ToolSuggested(tool='create_case', reason='Для корректной обработки спорной операции нужно зарегистрировать обращение.', params_hint={'intent': intent.value}),
        ]

    elif intent in {Intent.BlockCard, Intent.LostStolen}:
        summary = 'Клиент сообщает о потере, краже или необходимости срочно заблокировать карту.'
        risk_level = RiskLevel.high
        recurring = ['block_card']
        notes = 'Подтвердить ситуацию, получить согласие на блокировку и при необходимости зафиксировать кейс.'
        missing = ['customer_confirm_block']
        if wants_block or intent == Intent.LostStolen:
            missing = []
            phase = Phase.Act
        next_questions = [
            'Подтвердите, пожалуйста, что карту нужно заблокировать прямо сейчас.',
            'Карта потеряна, украдена или есть риск компрометации реквизитов?',
        ]
        tools = [
            ToolSuggested(tool='block_card', reason='Нужно срочно снизить риск повторных списаний.', params_hint={}),
            ToolSuggested(tool='create_case', reason='Нужно зафиксировать риск и дальнейшие действия.', params_hint={'intent': intent.value}),
        ]

    elif intent == Intent.StatusWhatNext:
        summary = 'Клиент уточняет статус обращения или спрашивает о следующем шаге.'
        risk_level = RiskLevel.low
        recurring = ['status_check']
        notes = 'Нужен подтвержденный номер обращения или контекст кейса.'
        phase = Phase.Explain if has_case_id else Phase.Collect
        missing = [] if has_case_id else ['case_id']
        next_questions = ['Подскажите номер обращения или уточните, по какой операции нужен статус.']
        tools = [
            ToolSuggested(tool='get_case_status', reason='Нужно вернуть подтвержденный статус кейса.', params_hint={}),
        ]

    danger = []
    if has('мошен', 'подозр', 'удаленный доступ', 'anydesk', 'teamviewer', 'безопасный счет'):
        danger.append(DangerFlag(type='scam_suspected', severity=Severity.high, text='Возможны признаки мошенничества или социнжиниринга.'))

    facts = AnalyzeFacts(
        card_hint=None,
        txn_hint=None,
        amount=None,
        currency=None,
        datetime_hint=None,
        merchant_hint=None,
        channel_hint=ChannelHint.unknown,
        customer_claim='unknown',
        card_in_possession='yes' if has_card else 'unknown',
        delivery_pref=None,
        previous_actions=[],
    )

    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=phase,
        confidence=0.84 if intent != Intent.Unknown else 0.42,
        summary_public=summary,
        risk_level=risk_level,
        facts=facts,
        profile_update=ProfileUpdate(
            client_card_context=summary,
            recurring_issues=recurring,
            notes_for_case_file=notes,
        ),
        missing_fields=missing,
        next_questions=next_questions,
        tools_suggested=tools,
        danger_flags=danger,
        risk_checklist=_risk_checklist(),
        analytics_tags=recurring,
    )


def draft(an: AnalyzeV1, plan: Plan, tools_ui: list[ToolUI], sources: list[SourceOut]) -> DraftV1:
    ghost = 'Понимаю. Чтобы проверить ситуацию и корректно зафиксировать обращение, уточните, пожалуйста: карта сейчас у вас на руках? Также подтвердите сумму и примерное время операции. После этого подскажу дальнейшие шаги.'
    operator_notes = 'Сначала соберите подтверждения по операции и наличие карты у клиента. Затем выполните сверку операций и оформите обращение.' if an.intent == Intent.SuspiciousTransaction else 'Соберите данные и выберите следующий шаг.'

    if an.intent in {Intent.BlockCard, Intent.LostStolen}:
        ghost = 'Понял. Для безопасности карты уточните, пожалуйста, нужно ли заблокировать ее прямо сейчас. После подтверждения подскажу следующий шаг и помогу зафиксировать обращение.'
        operator_notes = 'При подтверждении высокого риска сначала блокировка, затем фиксация кейса и рекомендации по перевыпуску.'
    elif an.intent == Intent.StatusWhatNext:
        ghost = 'Понял. Подскажите номер обращения или уточните, по какой операции нужен статус, и я подскажу следующий шаг без догадок и лишней самодеятельности.'
        operator_notes = 'Нужен подтвержденный идентификатор кейса или контекст обращения; статус сообщать только после tool_result.'

    qc = [
        QuickCard(title='Уточнить: карта у клиента?', insert_text='Подтвердите, пожалуйста, карта сейчас у вас на руках (да/нет)?', kind=QuickCardKind.question),
        QuickCard(title='Уточнить сумму/время', insert_text='Подтвердите сумму и примерное время операции, чтобы я мог(ла) сверить данные.', kind=QuickCardKind.question),
        QuickCard(title='Подтверждение: операция не ваша', insert_text='Подтвердите, пожалуйста, что вы не совершали эту операцию.', kind=QuickCardKind.confirmation),
        QuickCard(title='Предупреждение о кодах', insert_text='Пожалуйста, не сообщайте никому коды из SMS/Push и данные карты. Мы их не запрашиваем.', kind=QuickCardKind.instruction),
    ]

    fc = []
    if an.intent == Intent.SuspiciousTransaction:
        fc = [
            FormCard(
                title='Черновик обращения (create_case)',
                fields=[
                    FormField(key='intent', label='Тип обращения', value=an.intent.value),
                    FormField(key='txn_amount', label='Сумма (если подтверждено)', value=None),
                    FormField(key='txn_datetime', label='Дата/время (если подтверждено)', value=None),
                    FormField(key='customer_claim', label='Заявление клиента', value='not_mine'),
                ],
            )
        ]

    sidebar = Sidebar(
        phase=an.phase,
        intent=an.intent,
        plan=plan,
        facts_preview=FactsPreview(
            card_hint=an.facts.card_hint,
            txn_hint=an.facts.txn_hint,
            amount=an.facts.amount,
            datetime_hint=an.facts.datetime_hint,
            merchant_hint=an.facts.merchant_hint,
        ),
        sources=sources,
        tools=tools_ui,
        risk_checklist=an.risk_checklist,
        danger_flags=an.danger_flags,
        operator_notes=operator_notes,
    )

    return DraftV1(
        schema_version='1.0',
        ghost_text=ghost,
        quick_cards=qc,
        form_cards=fc,
        sidebar=sidebar,
    )


def _summary_for_tool(tool: str, tool_result: dict[str, Any]) -> tuple[str, list[QuickCard], str]:
    if tool == 'create_case':
        case_id = tool_result.get('case_id')
        ghost = 'Обращение зарегистрировано. Сообщите клиенту номер обращения и объясните, что статус можно уточнить позже, а решение зависит от проверки.'
        qc = [
            QuickCard(
                title='Сообщить номер обращения',
                insert_text=f'Обращение зарегистрировано. Номер обращения: {case_id}. Статус можно будет уточнить позже в чате или по стандартному каналу поддержки.',
                kind=QuickCardKind.status,
            ),
            QuickCard(
                title='Следующий шаг',
                insert_text='При необходимости уточните дополнительные данные по операции и предупредите клиента не сообщать коды из SMS/Push.',
                kind=QuickCardKind.instruction,
            ),
        ]
        return ghost, qc, 'Обращение зарегистрировано.'

    if tool == 'get_transactions':
        tx_count = len(tool_result.get('transactions') or [])
        ghost = 'Список операций получен. Теперь можно сверить спорную транзакцию и, если подтверждается оспаривание, оформить обращение.'
        qc = [
            QuickCard(
                title='Сверить транзакцию',
                insert_text=f'Получен список операций ({tx_count}). Сверьте сумму, дату и торговую точку со словами клиента.',
                kind=QuickCardKind.instruction,
            ),
            QuickCard(
                title='Оформить обращение',
                insert_text='Если спорная операция подтверждается, зарегистрируйте обращение и объясните клиенту дальнейшие шаги.',
                kind=QuickCardKind.status,
            ),
        ]
        return ghost, qc, 'Список операций получен.'

    if tool == 'get_case_status':
        status = tool_result.get('status') or 'unknown'
        ghost = 'Статус обращения получен. Передайте клиенту только подтвержденный статус без обещаний по срокам и результату.'
        qc = [
            QuickCard(
                title='Сообщить статус',
                insert_text=f'Текущий статус обращения: {status}. Финальное решение зависит от результата рассмотрения.',
                kind=QuickCardKind.status,
            ),
        ]
        return ghost, qc, 'Статус обращения получен.'

    if tool == 'block_card':
        ghost = 'Карта заблокирована. Сообщите клиенту, что операция выполнена, и объясните следующий безопасный шаг.'
        qc = [
            QuickCard(
                title='Подтвердить блокировку',
                insert_text='Карта заблокирована. При необходимости можно обсудить перевыпуск или дальнейшие действия по обращению.',
                kind=QuickCardKind.status,
            ),
        ]
        return ghost, qc, 'Карта заблокирована.'

    ghost = 'Действие выполнено. Передайте клиенту только подтвержденный результат и предложите следующий безопасный шаг.'
    qc = [
        QuickCard(
            title='Следующий шаг',
            insert_text='Подтвердите клиенту выполненное действие и уточните, нужна ли еще помощь по обращению.',
            kind=QuickCardKind.status,
        ),
    ]
    return ghost, qc, 'Инструмент выполнен.'


def explain(tool: str, tool_result: dict[str, Any], plan: Plan) -> ExplainV1:
    ghost, qc, summary = _summary_for_tool(tool, tool_result)
    return ExplainV1(
        schema_version='1.0',
        ghost_text=ghost,
        updates=ExplainUpdates(phase=Phase.Explain, plan=plan),
        quick_cards=qc,
        result_summary_public=summary,
        danger_flags=[],
        risk_checklist=_risk_checklist(),
    )
