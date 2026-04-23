from __future__ import annotations

import re
from typing import Any

from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    CardState,
    ChannelHint,
    CompromiseSignal,
    DangerFlag,
    DisputeSubtype,
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
    RequestedAction,
    RiskChecklistItem,
    RiskLevel,
    Severity,
    Sidebar,
    SourceOut,
    StatusContext,
    ToolSuggested,
    ToolUI,
)
from libs.common.analyze_guardrails import normalize_analyze
from libs.common.case_readiness import build_missing_field_meta, build_readiness


def _risk_checklist() -> list[RiskChecklistItem]:
    return [
        RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.'),
        RiskChecklistItem(id='no_pin', severity=Severity.high, text='Не запрашивать ПИН-код.'),
        RiskChecklistItem(id='no_sms_codes', severity=Severity.high, text='Не запрашивать одноразовые коды из SMS/Push.'),
        RiskChecklistItem(id='no_full_pan', severity=Severity.high, text='Не запрашивать полный номер карты.'),
        RiskChecklistItem(id='no_refund_promise', severity=Severity.medium, text='Не обещать возврат средств; исход зависит от рассмотрения.'),
        RiskChecklistItem(id='anti_remote_access', severity=Severity.high, text='Не рекомендовать удаленный доступ/установку приложений.'),
    ]


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _amount(text: str) -> float | None:
    m = re.search(r'(\d{2,7})(?:[\.,](\d{1,2}))?\s*(rub|руб|₽)?', text)
    if not m:
        return None
    frac = m.group(2) or '0'
    return float(f"{m.group(1)}.{frac}")


def _channel(text: str) -> ChannelHint:
    if _has(text, r'банкомат', r'atm', r'сняти\w*\s+налич'):
        return ChannelHint.atm
    if _has(text, r'терминал', r'pos', r'магазин'):
        return ChannelHint.pos
    if _has(text, r'онлайн', r'internet', r'интернет', r'сайт', r'приложени'):
        return ChannelHint.online
    return ChannelHint.unknown


def _subtype(text: str) -> DisputeSubtype:
    if _has(text, r'подписк', r'регулярн', r'recurring'):
        return DisputeSubtype.recurring_subscription
    if _has(text, r'дважды', r'двойн\w*\s+списан', r'дубликат'):
        return DisputeSubtype.duplicate_charge
    if _has(text, r'холд', r'hold', r'резерв', r'pending', r'незаверш', r'reversal', r'реверс'):
        return DisputeSubtype.reversal_pending
    if _has(text, r'банкомат', r'сняти\w*\s+налич'):
        return DisputeSubtype.cash_withdrawal
    if _has(text, r'товар\s+не\s+пришел', r'услуг\w*\s+не\s+оказан', r'мерчант'):
        return DisputeSubtype.merchant_dispute
    if _has(text, r'терминал', r'картой\s+оплатил'):
        return DisputeSubtype.card_present
    if _has(text, r'не\s+совершал', r'не\s+совершала', r'не\s+моя', r'подозр', r'мошен', r'списан'):
        return DisputeSubtype.suspicious
    return DisputeSubtype.unknown


def _card_state(text: str) -> CardState:
    if _has(text, r'украл', r'украли', r'краж', r'похитили'):
        return CardState.stolen
    if _has(text, r'потерял', r'потеряла', r'потеряна', r'утеря', r'карта\s+утеряна', r'пропала\s+карт'):
        return CardState.lost
    if _has(text, r'заблокирова'):
        return CardState.blocked
    if _has(text, r'поврежден', r'повреждена', r'сломал', r'чип\s+не\s+работает', r'магнитная\s+полоса'):
        return CardState.damaged
    if _has(text, r'карта\s+(у\s+меня|на\s+руках|со\s+мной|при\s+мне)'):
        return CardState.with_client
    return CardState.unknown


def _requested_actions(text: str) -> list[RequestedAction]:
    out: list[RequestedAction] = []
    if _has(text, r'заблокир'):
        out.append(RequestedAction.block_card)
    if _has(text, r'разблокир'):
        out.append(RequestedAction.unblock_card)
    if _has(text, r'перевыпуск', r'перевыпустить', r'нов\w*\s+карт'):
        out.append(RequestedAction.reissue_card)
    if _has(text, r'статус', r'что\s+дальше', r'номер\s+обращени', r'когда\s+рассмотрят'):
        out.append(RequestedAction.get_case_status)
    if _has(text, r'операц', r'списан', r'провер', r'подписк', r'дубликат', r'дважды', r'не\s+совершал', r'не\s+совершала', r'не\s+моя', r'холд', r'резерв'):
        out.append(RequestedAction.investigate_transaction)
    return list(dict.fromkeys(out))


def _status_context(text: str) -> StatusContext:
    if _has(text, r'обращени\w*\s+закрыт', r'решен', r'resolved'):
        return StatusContext.resolved
    if _has(text, r'в\s+работе', r'на\s+рассмотрени', r'жду\s+решени'):
        return StatusContext.waiting_review
    if _has(text, r'номер\s+обращени', r'case[-_ ]?\w+', r'статус\s+обращени'):
        return StatusContext.case_known
    if _has(text, r'что\s+дальше', r'какой\s+статус', r'когда\s+рассмотрят'):
        return StatusContext.case_unknown
    return StatusContext.unknown


def _compromise_signals(text: str) -> list[CompromiseSignal]:
    out: list[CompromiseSignal] = []
    if _has(text, r'код\w*\s+из\s+(sms|смс)', r'сообщил\w*\s+код', r'одноразов\w*\s+код'):
        out.append(CompromiseSignal.sms_code_shared)
    if _has(text, r'безопасн\w*\s+счет'):
        out.append(CompromiseSignal.safe_account)
    if _has(text, r'anydesk', r'teamviewer', r'удаленн\w*\s+доступ'):
        out.append(CompromiseSignal.remote_access)
    if _has(text, r'подменн\w*\s+номер', r'служб\w*\s+безопасност', r'звон\w*\s+из\s+банка'):
        out.append(CompromiseSignal.spoofed_call)
    if _has(text, r'cvv', r'cvc'):
        out.append(CompromiseSignal.cvv_shared)
    return list(dict.fromkeys(out))


def _intent(text: str) -> Intent:
    status = _status_context(text)
    state = _card_state(text)
    actions = _requested_actions(text)
    subtype = _subtype(text)

    if status != StatusContext.unknown:
        return Intent.StatusWhatNext
    if state in {CardState.lost, CardState.stolen}:
        if subtype == DisputeSubtype.suspicious and RequestedAction.reissue_card in actions:
            return Intent.UnblockReissue
        return Intent.LostStolen
    if RequestedAction.unblock_card in actions or RequestedAction.reissue_card in actions:
        if _has(text, r'не\s+работает\s+карт', r'не\s+проход\w*\s+оплат', r'онлайн[- ]?платеж\s+не\s+проход') and RequestedAction.reissue_card in actions and not _explicit_reissue(text):
            return Intent.CardNotWorking
        return Intent.UnblockReissue
    if subtype != DisputeSubtype.unknown or _compromise_signals(text):
        return Intent.SuspiciousTransaction
    if _has(text, r'не\s+работает\s+карт', r'не\s+проход\w*\s+оплат', r'онлайн[- ]?платеж\s+не\s+проход', r'чип\s+не\s+работает', r'банкомат\s+не\s+читает'):
        return Intent.CardNotWorking
    if RequestedAction.block_card in actions:
        return Intent.BlockCard
    return Intent.Unknown


def _tools(intent: Intent, subtype: DisputeSubtype) -> list[ToolSuggested]:
    if intent == Intent.StatusWhatNext:
        return [ToolSuggested(tool='get_case_status', reason='Нужно вернуть подтвержденный статус обращения.', params_hint={})]
    if intent == Intent.LostStolen:
        return [
            ToolSuggested(tool='block_card', reason='Нужно срочно снизить риск повторных списаний.', params_hint={}),
            ToolSuggested(tool='reissue_card', reason='После блокировки может потребоваться перевыпуск карты.', params_hint={}),
            ToolSuggested(tool='create_case', reason='Нужно зафиксировать обращение и признаки риска.', params_hint={'intent': intent.value}),
        ]
    if intent == Intent.UnblockReissue:
        return [
            ToolSuggested(tool='get_case_status', reason='Нужно понять текущее состояние блокировки или обращения.', params_hint={}),
            ToolSuggested(tool='unblock_card', reason='Разблокировка допустима только после проверки статуса и причины блокировки.', params_hint={}),
            ToolSuggested(tool='reissue_card', reason='При утрате, повреждении или компрометации может понадобиться перевыпуск.', params_hint={}),
        ]
    if intent == Intent.CardNotWorking:
        return [
            ToolSuggested(tool='get_card_limits', reason='Нужно проверить лимиты и настройки карты.', params_hint={}),
            ToolSuggested(tool='toggle_online_payments', reason='Нужно проверить, не отключены ли онлайн-платежи.', params_hint={}),
            ToolSuggested(tool='reissue_card', reason='При повреждении карты может потребоваться перевыпуск.', params_hint={}),
        ]
    if intent == Intent.BlockCard:
        return [
            ToolSuggested(tool='block_card', reason='Нужно выполнить подтвержденную блокировку карты.', params_hint={}),
            ToolSuggested(tool='create_case', reason='Нужно зафиксировать риск и дальнейшие действия.', params_hint={'intent': intent.value}),
        ]
    if intent == Intent.SuspiciousTransaction:
        reason = 'Нужно сверить детали спорной операции по списку транзакций.'
        if subtype == DisputeSubtype.recurring_subscription:
            reason = 'Нужно проверить регулярное списание или подписку и подтвердить детали операции.'
        elif subtype == DisputeSubtype.duplicate_charge:
            reason = 'Нужно сравнить дублирующиеся операции и исключить повторное списание.'
        elif subtype == DisputeSubtype.reversal_pending:
            reason = 'Нужно проверить статус холда или незавершенного списания.'
        return [
            ToolSuggested(tool='get_transactions', reason=reason, params_hint={'date_range': 'последние 7 дней'}),
            ToolSuggested(tool='create_case', reason='Нужно зарегистрировать обращение по спорной операции.', params_hint={'intent': intent.value}),
        ]
    return []


def _missing_and_questions(intent: Intent, subtype: DisputeSubtype, actions: list[RequestedAction], status: StatusContext) -> tuple[list[str], list[str], Phase]:
    if intent == Intent.StatusWhatNext:
        missing = [] if status == StatusContext.case_known else ['case_id']
        questions = ['Подскажите номер обращения или уточните, по какой операции нужен статус.']
        return missing, questions, Phase.Explain if not missing else Phase.Collect

    if intent == Intent.LostStolen:
        questions = [
            'Подтвердите, пожалуйста, что карту нужно заблокировать прямо сейчас.',
            'Были ли уже неизвестные операции после утраты или кражи карты?',
            'Нужен ли перевыпуск карты после блокировки?',
        ]
        return ([] if RequestedAction.block_card in actions else ['customer_confirm_block']), questions, (Phase.Act if RequestedAction.block_card in actions else Phase.Collect)

    if intent == Intent.UnblockReissue:
        missing = ['case_id'] if RequestedAction.unblock_card in actions else []
        questions = [
            'Карта уже заблокирована и требуется разблокировка или нужен перевыпуск?',
            'Подтвердите, пожалуйста, причину перевыпуска или разблокировки.',
        ]
        return missing, questions, (Phase.Act if not missing else Phase.Collect)

    if intent == Intent.CardNotWorking:
        return ['problem_channel_confirm'], [
            'Где именно не работает карта: в магазине, онлайн или в банкомате?',
            'Карта физически повреждена или проблема только в конкретном сценарии оплаты?',
        ], Phase.Collect

    if intent == Intent.BlockCard:
        return ([] if RequestedAction.block_card in actions else ['customer_confirm_block']), ['Подтвердите, пожалуйста, что вы хотите заблокировать карту сейчас.'], (Phase.Act if RequestedAction.block_card in actions else Phase.Collect)

    if intent == Intent.SuspiciousTransaction:
        missing = ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm']
        if RequestedAction.block_card in actions:
            missing.append('customer_confirm_block')
        questions = [
            'Подтвердите, пожалуйста, карта сейчас у вас на руках (да/нет).',
            'Подтвердите сумму спорной операции.',
            'Подтвердите примерные дату и время спорной операции.',
        ]
        if subtype == DisputeSubtype.recurring_subscription:
            missing.append('merchant_name_confirm')
            questions.append('Подскажите, как называется подписка или сервис, по которому прошло списание?')
        elif subtype == DisputeSubtype.duplicate_charge:
            questions.append('Подтвердите, пожалуйста, что списание прошло дважды по одной и той же операции.')
        elif subtype == DisputeSubtype.reversal_pending:
            questions.append('Подтвердите, пожалуйста, что вы видите холд или резерв по операции, а не окончательное списание.')
        return list(dict.fromkeys(missing)), questions, Phase.Collect

    return [], [], Phase.Collect


def analyze(history_redacted: str) -> AnalyzeV1:
    text = history_redacted.lower()
    intent = _intent(text)
    subtype = _subtype(text)
    card_state = _card_state(text)
    actions = _requested_actions(text)
    status_context = _status_context(text)
    compromise = _compromise_signals(text)
    missing, next_questions, phase = _missing_and_questions(intent, subtype, actions, status_context)

    risk = RiskLevel.low
    if compromise or intent in {Intent.LostStolen, Intent.BlockCard}:
        risk = RiskLevel.high
    elif intent != Intent.Unknown:
        risk = RiskLevel.medium

    summary = 'Клиент обратился с вопросом по карте.'
    if intent == Intent.SuspiciousTransaction:
        if subtype == DisputeSubtype.recurring_subscription:
            summary = 'Клиент сообщает о спорном регулярном списании или подписке.'
        elif subtype == DisputeSubtype.duplicate_charge:
            summary = 'Клиент сообщает о возможном двойном списании.'
        elif subtype == DisputeSubtype.reversal_pending:
            summary = 'Клиент уточняет ситуацию по холду или незавершенному списанию.'
        else:
            summary = 'Клиент сообщает о спорной или подозрительной операции.'
    elif intent == Intent.LostStolen:
        summary = 'Клиент сообщает об утрате или краже карты.'
    elif intent == Intent.UnblockReissue:
        summary = 'Клиент просит разблокировать карту или оформить перевыпуск.'
    elif intent == Intent.CardNotWorking:
        summary = 'Клиент сообщает, что карта не работает.'
    elif intent == Intent.StatusWhatNext:
        summary = 'Клиент уточняет статус обращения или следующий шаг.'

    danger = []
    if compromise:
        danger.append(DangerFlag(type='scam_suspected', severity=Severity.high, text='Есть признаки мошенничества или социальной инженерии.'))

    raw = AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=phase,
        confidence=0.82 if intent != Intent.Unknown else 0.42,
        summary_public=summary,
        risk_level=risk,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=_amount(text),
            currency='RUB' if _has(text, r'rub', r'руб', r'₽') else None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint=_channel(text),
            customer_claim='not_mine' if intent == Intent.SuspiciousTransaction else 'unknown',
            card_in_possession='yes' if card_state == CardState.with_client else ('no' if card_state in {CardState.lost, CardState.stolen} else 'unknown'),
            delivery_pref=None,
            previous_actions=[],
            dispute_subtype=subtype,
            card_state=card_state,
            requested_actions=actions,
            status_context=status_context,
            compromise_signals=compromise,
        ),
        profile_update=ProfileUpdate(
            client_card_context=summary,
            recurring_issues=[subtype.value] if subtype != DisputeSubtype.unknown else [],
            notes_for_case_file=summary,
        ),
        missing_fields=missing,
        next_questions=next_questions,
        tools_suggested=_tools(intent, subtype),
        danger_flags=danger,
        risk_checklist=_risk_checklist(),
        analytics_tags=[intent.value.lower(), subtype.value],
    )
    return normalize_analyze(history_redacted, raw)


def draft(an: AnalyzeV1, plan: Plan, tools_ui: list[ToolUI], sources: list[SourceOut]) -> DraftV1:
    ghost = 'Понял. Уточните, пожалуйста, ключевые детали, после чего подскажу следующий безопасный шаг.'
    operator_notes = 'Соберите обязательные данные и выполните следующий шаг только после подтверждения клиента.'

    if an.intent == Intent.SuspiciousTransaction:
        if an.facts.dispute_subtype == DisputeSubtype.recurring_subscription:
            ghost = 'Понимаю. Чтобы проверить спорное регулярное списание, уточните, пожалуйста, название сервиса или подписки, сумму и примерное время операции.'
        elif an.facts.dispute_subtype == DisputeSubtype.duplicate_charge:
            ghost = 'Понимаю. Чтобы проверить возможное двойное списание, уточните, пожалуйста, сумму, примерное время операции и подтвердите, что списание прошло дважды.'
        elif an.facts.dispute_subtype == DisputeSubtype.reversal_pending:
            ghost = 'Понимаю. Чтобы разобраться с холдом или резервом, уточните, пожалуйста, примерное время операции и подтвердите, что вы видите именно незавершенное списание.'
        else:
            ghost = 'Понимаю. Чтобы проверить ситуацию и корректно зафиксировать обращение, уточните, пожалуйста: карта сейчас у вас на руках, какова сумма и примерное время операции?'
        operator_notes = 'Сначала соберите подтверждения по операции, затем переходите к сверке и оформлению обращения.'
    elif an.intent == Intent.LostStolen:
        ghost = 'Понял. Для безопасности карты уточните, пожалуйста, нужно ли заблокировать ее прямо сейчас и были ли уже неизвестные операции после утраты или кражи.'
        operator_notes = 'При утрате или краже сначала блокировка, затем фиксация кейса и обсуждение перевыпуска.'
    elif an.intent == Intent.UnblockReissue:
        ghost = 'Понял. Уточните, пожалуйста, нужен перевыпуск карты или вы хотите разблокировать уже заблокированную карту.'
        operator_notes = 'Сначала определить, идет речь о разблокировке или перевыпуске, затем проверять допустимое действие.'
    elif an.intent == Intent.CardNotWorking:
        ghost = 'Понял. Подскажите, пожалуйста, где именно не работает карта: в магазине, онлайн или в банкомате, и есть ли признаки повреждения карты.'
        operator_notes = 'Сначала определить канал проблемы и исключить ограничения или повреждение карты.'
    elif an.intent == Intent.StatusWhatNext:
        ghost = 'Понял. Подскажите номер обращения или уточните, по какой операции нужен статус, и я подскажу следующий шаг без догадок.'
        operator_notes = 'Статус сообщать только после подтвержденного результата get_case_status.'

    qc = [QuickCard(title='Следующий вопрос', insert_text=q, kind=QuickCardKind.question) for q in (an.next_questions[:3] or ['Уточните, пожалуйста, подробности обращения.'])]
    qc.append(QuickCard(title='Предупреждение о кодах', insert_text='Пожалуйста, не сообщайте никому коды из SMS/Push и данные карты. Мы этого не запрашиваем.', kind=QuickCardKind.instruction))

    fc = []
    if an.intent == Intent.SuspiciousTransaction:
        fc = [FormCard(title='Черновик обращения (create_case)', fields=[
            FormField(key='intent', label='Тип обращения', value=an.intent.value),
            FormField(key='dispute_subtype', label='Подтип спора', value=an.facts.dispute_subtype.value),
            FormField(key='txn_amount', label='Сумма (если подтверждено)', value=an.facts.amount),
            FormField(key='customer_claim', label='Заявление клиента', value=an.facts.customer_claim),
        ])]

    missing_fields_meta = build_missing_field_meta(an.intent, an.missing_fields)
    readiness = build_readiness(intent=an.intent, missing_fields=an.missing_fields, tools=tools_ui, case_status='open')

    sidebar = Sidebar(
        phase=an.phase,
        intent=an.intent,
        plan=plan,
        facts_preview=FactsPreview(
            card_hint=an.facts.card_hint,
            txn_hint=an.facts.txn_hint or an.facts.dispute_subtype.value,
            amount=an.facts.amount,
            datetime_hint=an.facts.datetime_hint,
            merchant_hint=an.facts.merchant_hint,
        ),
        sources=sources,
        tools=tools_ui,
        missing_fields_meta=missing_fields_meta,
        readiness=readiness,
        risk_checklist=an.risk_checklist,
        danger_flags=an.danger_flags,
        operator_notes=operator_notes,
    )

    return DraftV1(schema_version='1.0', ghost_text=ghost, quick_cards=qc, form_cards=fc, sidebar=sidebar)


def _summary_for_tool(tool: str, tool_result: dict[str, Any]) -> tuple[str, list[QuickCard], str]:
    if tool == 'create_case':
        case_id = tool_result.get('case_id') or '<unknown_case>'
        ghost = 'Обращение зарегистрировано. Сообщите клиенту номер обращения и объясните, что статус можно уточнить позже, а решение зависит от проверки.'
        qc = [
            QuickCard(title='Сообщить номер обращения', insert_text=f'Обращение зарегистрировано. Номер обращения: {case_id}. Статус можно будет уточнить позже по стандартному каналу.', kind=QuickCardKind.status),
            QuickCard(title='Следующий шаг', insert_text='При необходимости уточните дополнительные данные по операции и предупредите клиента не сообщать коды из SMS/Push.', kind=QuickCardKind.instruction),
        ]
        return ghost, qc, 'Обращение зарегистрировано.'

    if tool == 'get_case_status':
        status = tool_result.get('status') or 'unknown'
        ghost = 'Статус обращения получен. Передайте клиенту только подтвержденный статус без обещаний по срокам и результату.'
        qc = [QuickCard(title='Сообщить статус', insert_text=f'Текущий статус обращения: {status}. Финальное решение зависит от результата рассмотрения.', kind=QuickCardKind.status)]
        return ghost, qc, 'Статус обращения получен.'

    if tool == 'block_card':
        ghost = 'Карта заблокирована. Сообщите клиенту, что операция выполнена, и объясните следующий безопасный шаг.'
        qc = [QuickCard(title='Подтвердить блокировку', insert_text='Карта заблокирована. При необходимости можно обсудить перевыпуск или дальнейшие действия по обращению.', kind=QuickCardKind.status)]
        return ghost, qc, 'Карта заблокирована.'

    ghost = 'Действие выполнено. Передайте клиенту только подтвержденный результат и предложите следующий безопасный шаг.'
    qc = [QuickCard(title='Следующий шаг', insert_text='Подтвердите клиенту выполненное действие и уточните, нужна ли еще помощь по обращению.', kind=QuickCardKind.status)]
    return ghost, qc, 'Инструмент выполнен.'


def explain(tool: str, tool_result: dict[str, Any], plan: Plan) -> ExplainV1:
    ghost, qc, summary = _summary_for_tool(tool, tool_result)
    return ExplainV1(schema_version='1.0', ghost_text=ghost, updates=ExplainUpdates(phase=Phase.Explain, plan=plan), quick_cards=qc, result_summary_public=summary, danger_flags=[], risk_checklist=_risk_checklist())
