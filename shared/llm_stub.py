from __future__ import annotations

from typing import Any
import re

from contracts.schemas import (
    AnalyzeV1, AnalyzeFacts, ProfileUpdate, ToolSuggested, Intent, Phase, RiskLevel,
    ChannelHint, DangerFlag, Severity, RiskChecklistItem,
    DraftV1, QuickCard, QuickCardKind, FormCard, FormField, Sidebar, FactsPreview, SourceOut,
    Plan, ToolUI,
    ExplainV1, ExplainUpdates,
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
    # простая эвристика: если есть слова "списание"/"не я" → подозрительная операция
    text = history_redacted.lower()
    intent = Intent.Unknown
    if any(w in text for w in ['списан', 'операц', 'не совершал', 'не я', 'мошен']):
        intent = Intent.SuspiciousTransaction

    phase = Phase.Collect
    missing = []
    if intent == Intent.SuspiciousTransaction:
        missing = ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block']

        # простая детерминированная логика: если нужные ответы уже есть в истории, двигаем фазу
        has_card = bool(re.search(r'карта\s+(у\s+меня|на\s+руках|со\s+мной)', text))
        has_amount = bool(re.search(r'\b\d{2,7}\b', text))
        has_time = bool(re.search(r'(\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b|вчера|сегодня)', text))
        wants_block = bool(re.search(r'(заблокир(уйте|овать)|блокир(уйте|овать))', text))
        if has_card and has_amount and has_time:
            missing = []
            phase = Phase.Act
        elif has_card and has_amount:
            missing = ['txn_datetime_confirm', 'customer_confirm_block']
        if wants_block and 'customer_confirm_block' in missing:
            missing.remove('customer_confirm_block')

    danger = []
    if any(w in text for w in ['мошен', 'неизвестн', 'подозр']):
        danger.append(DangerFlag(type='scam_suspected', severity=Severity.high, text='Возможны признаки мошенничества/социнжиниринга.'))

    facts = AnalyzeFacts(
        card_hint=None,
        txn_hint=None,
        amount=None,
        currency=None,
        datetime_hint=None,
        merchant_hint=None,
        channel_hint=ChannelHint.unknown,
        customer_claim='unknown',
        card_in_possession='unknown',
        delivery_pref=None,
        previous_actions=[],
    )

    tools = []
    if intent == Intent.SuspiciousTransaction:
        tools = [
            ToolSuggested(tool='get_transactions', reason='Нужно сверить детали спорной операции по списку транзакций.', params_hint={'date_range': 'последние 7 дней'}),
            ToolSuggested(tool='create_case', reason='Для корректной обработки спорной операции нужно зарегистрировать обращение.', params_hint={'intent': intent.value}),
        ]

    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=phase,
        confidence=0.8 if intent != Intent.Unknown else 0.4,
        summary_public='Клиент сообщает о спорной/подозрительной операции и просит проверить ситуацию.' if intent == Intent.SuspiciousTransaction else 'Клиент обратился с вопросом по карте.',
        risk_level=RiskLevel.high if intent == Intent.SuspiciousTransaction else RiskLevel.low,
        facts=facts,
        profile_update=ProfileUpdate(
            client_card_context='Сообщение клиента о спорной операции; требуется проверка и фиксация обращения.' if intent == Intent.SuspiciousTransaction else '',
            recurring_issues=['suspicious_transaction'] if intent == Intent.SuspiciousTransaction else [],
            notes_for_case_file='Уточнить детали операции и наличие карты у клиента.' if intent == Intent.SuspiciousTransaction else '',
        ),
        missing_fields=missing,
        next_questions=(
            [
                'Подтвердите, пожалуйста, что карта сейчас находится у вас (да/нет).',
                'Подтвердите сумму и примерное время операции, чтобы сверить данные.',
                'Подтвердите, что вы не совершали эту операцию и хотите зафиксировать обращение.'
            ] if intent == Intent.SuspiciousTransaction else []
        ),
        tools_suggested=tools,
        danger_flags=danger,
        risk_checklist=_risk_checklist(),
        analytics_tags=['suspicious', 'needs_verification'] if intent == Intent.SuspiciousTransaction else [],
    )


def draft(an: AnalyzeV1, plan: Plan, tools_ui: list[ToolUI], sources: list[SourceOut]) -> DraftV1:
    ghost = 'Понимаю. Чтобы проверить ситуацию и корректно зафиксировать обращение, уточните, пожалуйста: карта сейчас у вас на руках? Также подтвердите сумму и примерное время операции. После этого подскажу дальнейшие шаги.'

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
        operator_notes='Сначала соберите подтверждения по операции и наличие карты у клиента. Затем выполните сверку операций и оформите обращение.' if an.intent == Intent.SuspiciousTransaction else 'Соберите данные и выберите следующий шаг.',
    )

    return DraftV1(
        schema_version='1.0',
        ghost_text=ghost,
        quick_cards=qc,
        form_cards=fc,
        sidebar=sidebar,
    )


def explain(tool: str, tool_result: dict[str, Any], plan: Plan) -> ExplainV1:
    ghost = 'Спасибо. Действие выполнено. Далее обращение будет рассмотрено, и при необходимости мы свяжемся для уточнений. Пожалуйста, не сообщайте никому коды из SMS/Push и данные карты.'

    # отметим шаги в плане
    steps = []
    for s in plan.steps:
        done = s.done
        if tool in ['get_transactions'] and s.id == 'act_get_txn':
            done = True
        if tool in ['create_case'] and s.id == 'case_create':
            done = True
        steps.append(s.model_copy(update={'done': done}))

    cur = plan.current_step_id
    if tool in ['create_case', 'get_transactions']:
        cur = 'explain_next'

    new_plan = plan.model_copy(update={'current_step_id': cur, 'steps': steps})

    qc = [
        QuickCard(title='Что будет дальше', insert_text='Обращение зарегистрировано. Статус и дальнейшие шаги можно уточнить в этом чате, мы сообщим, если понадобятся дополнительные сведения.', kind=QuickCardKind.status),
        QuickCard(title='Подтверждение блокировки', insert_text='Подтвердите, пожалуйста, хотите ли вы временно заблокировать карту для безопасности (да/нет)?', kind=QuickCardKind.question),
    ]

    return ExplainV1(
        schema_version='1.0',
        ghost_text=ghost,
        updates=ExplainUpdates(phase=Phase.Explain, plan=new_plan),
        quick_cards=qc,
        result_summary_public='Инструмент выполнен; клиенту направлены дальнейшие безопасные шаги.',
        danger_flags=[],
        risk_checklist=_risk_checklist(),
    )
