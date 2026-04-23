from __future__ import annotations

import re

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    ChannelHint,
    CompromiseSignal,
    DangerFlag,
    DisputeSubtype,
    Intent,
    Phase,
    RequestedAction,
    RiskLevel,
    Severity,
    StatusContext,
    ToolSuggested,
)

def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _unique_enums(items):
    out = []
    seen = set()
    for item in items:
        if item is None or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _merge_tags(current: list[str], previous: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in [*(current or []), *(previous or [])]:
        s = str(item or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _enum_value(value):
    return getattr(value, 'value', value)


def _coerce_channel_hint(value) -> ChannelHint:
    raw = str(_enum_value(value) or 'unknown').strip()
    raw = raw.split('.')[-1]
    try:
        return ChannelHint(raw)
    except Exception:
        return ChannelHint.unknown


def _detect_dispute_subtype(text: str) -> DisputeSubtype:
    if _has(text, r'подписк', r'recurring', r'регулярн'):
        return DisputeSubtype.recurring_subscription

    if _has(text, r'дважды', r'двойн\w*\s+списан', r'дубликат'):
        return DisputeSubtype.duplicate_charge

    if _has(text, r'холд', r'hold', r'резерв', r'pending', r'незаверш', r'reversal', r'реверс'):
        return DisputeSubtype.reversal_pending

    if _has(text, r'сняти\w*\s+налич', r'наличн', r'банкомат', r'atm'):
        return DisputeSubtype.cash_withdrawal

    merchant_dispute = _has(
        text,
        r'товар\s+не\s+пришел',
        r'товар\s+не\s+достав',
        r'заказ\s+не\s+пришел',
        r'заказ\s+не\s+достав',
        r'не\s+доставили\s+заказ',
        r'не\s+доставил[аи]?\s+заказ',
        r'не\s+получил\s+товар',
        r'не\s+получила\s+товар',
        r'не\s+получил\s+заказ',
        r'не\s+получила\s+заказ',
        r'услуг\w*\s+не\s+оказан',
        r'не\s+оказал[аи]?\s+услуг',
        r'деньги\s+не\s+вернул',
        r'деньги\s+не\s+вернули',
        r'возврат\s+не\s+пришел',
        r'возврат\s+не\s+поступил',
        r'магазин.*не\s+вернул',
        r'магазин.*не\s+достав',
        r'мерчант',
    )
    if merchant_dispute:
        return DisputeSubtype.merchant_dispute

    if _has(text, r'терминал', r'\bpos\b', r'в\s+магазине\s+не\s+работа', r'в\s+магазине\s+не\s+проход'):
        return DisputeSubtype.card_present

    negative_suspicious = _has(
        text,
        r'ничего\s+подозрительн\w*\s+не\s+было',
        r'не\s+было\s+ничего\s+подозрительн',
        r'ничего\s+подозрительн\w*\s+не\s+заметил',
        r'ничего\s+подозрительн\w*\s+не\s+заметила',
    )
    if negative_suspicious:
        return DisputeSubtype.unknown

    if _has(text, r'не\s+совершал', r'не\s+совершала', r'не\s+моя', r'мошен', r'подозр', r'спорн\w*\s+операц', r'оспорить\s+операц'):
        return DisputeSubtype.suspicious

    return DisputeSubtype.unknown


def _detect_card_state(text: str) -> CardState:
    if _has(
        text,
        r'карту?\s+не\s+терял',
        r'карту?\s+не\s+теряла',
        r'не\s+терял\s+карту',
        r'не\s+теряла\s+карту',
        r'карта\s+не\s+утеряна',
        r'карту?\s+не\s+украли',
        r'карту?\s+не\s+крали',
        r'карта\s+не\s+пропала',
    ):
        return CardState.with_client

    if _has(text, r'украл', r'украли', r'краж', r'похитили'):
        return CardState.stolen
    if _has(text, r'потерял', r'потеряла', r'потеряна', r'утеря', r'карта\s+утеряна', r'пропала\s+карт'):
        return CardState.lost
    if _has(text, r'заблокирова', r'карта\s+заблокирован'):
        return CardState.blocked
    if _has(text, r'поврежден', r'повреждена', r'сломал', r'чип\s+не\s+работает', r'магнитная\s+полоса'):
        return CardState.damaged
    if _has(
        text,
        r'карта\s+(у\s+меня|на\s+руках|со\s+мной|при\s+мне)',
        r'она\s+(у\s+меня|со\s+мной)',
        r'карт[ау]\s+при\s+мне',
        r'не\s+терял\s+ее',
        r'не\s+теряла\s+ее',
    ):
        return CardState.with_client
    return CardState.unknown


def _detect_card_possession(text: str) -> str:
    if _has(
        text,
        r'карта\s+(у\s+меня|на\s+руках|со\s+мной|при\s+мне)',
        r'она\s+(у\s+меня|со\s+мной)',
        r'карту?\s+не\s+терял',
        r'карту?\s+не\s+теряла',
        r'не\s+терял\s+карту',
        r'не\s+теряла\s+карту',
        r'карта\s+не\s+утеряна',
        r'карту?\s+не\s+украли',
        r'карту?\s+не\s+крали',
        r'карта\s+не\s+пропала',
    ):
        return 'yes'

    if _has(text, r'потерял', r'потеряла', r'потеряна', r'утеря', r'украл', r'украли', r'краж', r'похитили', r'пропала\s+карт'):
        return 'no'

    return 'unknown'


def _detect_channel_hint(text: str) -> str:
    online_fail = _has(
        text,
        r'онлайн[- ]?платеж\w*\s+не\s+проход',
        r'онлайн[- ]?оплат\w*\s+не\s+проход',
        r'в\s+интернет[е]?\s+не\s+проход',
        r'на\s+сайте\s+не\s+проход',
        r'не\s+работает\s+онлайн',
        r'не\s+проходит\s+онлайн',
        r'интернет[- ]?платеж',
        r'3ds',
    )
    atm_fail = _has(
        text,
        r'в\s+банкомате\s+не\s+работа',
        r'банкомат\s+не\s+читает',
        r'банкомат\s+не\s+принимает',
        r'\batm\b',
    )
    pos_fail = _has(
        text,
        r'в\s+магазине\s+не\s+работа',
        r'в\s+магазине\s+не\s+проход',
        r'терминал\w*\s+не\s+принима',
        r'pos',
    )

    if online_fail:
        return 'online'
    if atm_fail:
        return 'atm'
    if pos_fail:
        return 'pos'
    return 'unknown'


def _detect_requested_actions(text: str) -> list[RequestedAction]:
    out: list[RequestedAction] = []

    if _has(text, r'заблокир', r'блокировк\w*\s+карт'):
        out.append(RequestedAction.block_card)

    if _has(text, r'разблокир'):
        out.append(RequestedAction.unblock_card)

    if _has(text, r'перевыпуск', r'перевыпустить', r'нов\w*\s+карт'):
        out.append(RequestedAction.reissue_card)

    if _has(text, r'статус', r'что\s+дальше', r'когда\s+рассмотрят', r'номер\s+обращени'):
        out.append(RequestedAction.get_case_status)

    if _has(
        text,
        r'операц',
        r'списан',
        r'провер',
        r'не\s+моя',
        r'не\s+совершал',
        r'не\s+совершала',
        r'подписк',
        r'дубликат',
        r'дважды',
        r'холд',
        r'резерв',
        r'оспорить',
        r'оспарив',
        r'чарджб',
        r'вернуть\s+деньги',
        r'не\s+вернул',
        r'не\s+вернули',
        r'не\s+достав',
    ):
        out.append(RequestedAction.investigate_transaction)

    return _unique_enums(out)
    

def _detect_status_context(text: str) -> StatusContext:
    if _has(text, r'обращени\w*\s+закрыт', r'решен', r'resolved'):
        return StatusContext.resolved
    if _has(text, r'в\s+работе', r'на\s+рассмотрени', r'жду\s+решени', r'ожидаю\s+ответ'):
        return StatusContext.waiting_review
    if _has(text, r'номер\s+обращени', r'case[-_ ]?\w+', r'статус\s+обращени'):
        return StatusContext.case_known
    if _has(text, r'что\s+дальше', r'какой\s+статус', r'когда\s+рассмотрят'):
        return StatusContext.case_unknown
    return StatusContext.unknown


def _detect_compromise_signals(text: str) -> list[CompromiseSignal]:
    out: list[CompromiseSignal] = []
    if _has(text, r'код\w*\s+из\s+(sms|смс)', r'сообщил\w*\s+код', r'одноразов\w*\s+код', r'push[- ]?код'):
        out.append(CompromiseSignal.sms_code_shared)
    if _has(text, r'безопасн\w*\s+счет'):
        out.append(CompromiseSignal.safe_account)
    if _has(text, r'anydesk', r'teamviewer', r'удаленн\w*\s+доступ'):
        out.append(CompromiseSignal.remote_access)
    if _has(text, r'подменн\w*\s+номер', r'звон\w*\s+якобы\s+из\s+банка', r'звон\w*\s+из\s+банка', r'служб\w*\s+безопасност'):
        out.append(CompromiseSignal.spoofed_call)
    if _has(text, r'cvv', r'cvc'):
        out.append(CompromiseSignal.cvv_shared)
    return _unique_enums(out)


def _detect_card_not_working(text: str) -> bool:
    return _has(
        text,
        r'не\s+работает\s+карт',
        r'не\s+проход\w*\s+оплат',
        r'онлайн[- ]?платеж\s+не\s+проход',
        r'не\s+оплачивает',
        r'банкомат\s+не\s+читает',
        r'чип\s+не\s+работает',
    )


def _explicit_reissue(text: str) -> bool:
    return _has(text, r'нужен\s+перевыпуск', r'хочу\s+перевыпуск', r'хочу\s+перевыпустить', r'нужна\s+новая\s+карта')


def _detect_customer_claim(text: str) -> str:
    if _has(text, r'не\s+моя', r'не\s+совершал', r'не\s+совершала', r'не\s+узнаю\s+операц'):
        return 'not_mine'
    return 'unknown'


def _detect_intent(text: str) -> Intent:
    status_context = _detect_status_context(text)
    card_state = _detect_card_state(text)
    actions = _detect_requested_actions(text)
    subtype = _detect_dispute_subtype(text)
    compromise = _detect_compromise_signals(text)
    card_not_working = _detect_card_not_working(text)

    if status_context != StatusContext.unknown:
        return Intent.StatusWhatNext

    if card_state in {CardState.lost, CardState.stolen}:
        return Intent.LostStolen

    if RequestedAction.unblock_card in actions:
        return Intent.UnblockReissue

    if card_not_working:
        if _explicit_reissue(text) and RequestedAction.investigate_transaction not in actions and not compromise:
            return Intent.UnblockReissue
        return Intent.CardNotWorking

    if RequestedAction.reissue_card in actions and subtype == DisputeSubtype.unknown and not compromise and card_state not in {CardState.lost, CardState.stolen}:
        return Intent.UnblockReissue

    if subtype != DisputeSubtype.unknown or compromise:
        return Intent.SuspiciousTransaction

    if RequestedAction.block_card in actions:
        return Intent.BlockCard

    return Intent.Unknown


def _normalized_tools_for_intent(
    intent: Intent,
    subtype: DisputeSubtype,
    actions: list[RequestedAction],
    compromise: list[CompromiseSignal],
) -> list[ToolSuggested]:
    if intent == Intent.StatusWhatNext:
        return [ToolSuggested(tool='get_case_status', reason='Нужно вернуть подтвержденный статус обращения.', params_hint={})]

    if intent == Intent.LostStolen:
        return [
            ToolSuggested(tool='block_card', reason='Нужно срочно снизить риск повторных списаний или компрометации карты.', params_hint={}),
            ToolSuggested(tool='create_case', reason='Нужно зафиксировать обращение и признаки риска.', params_hint={'intent': intent.value}),
            ToolSuggested(tool='reissue_card', reason='После блокировки может потребоваться перевыпуск карты.', params_hint={}),
        ]

    if intent == Intent.UnblockReissue:
        return [
            ToolSuggested(tool='get_case_status', reason='Нужно понять текущее состояние обращения или блокировки.', params_hint={}),
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
            ToolSuggested(tool='block_card', reason='Блокировка нужна только если клиент подтверждает действие или есть отдельный риск-компонент.', params_hint={}),
        ]

    return []


def _normalized_missing_fields(
    intent: Intent,
    subtype: DisputeSubtype,
    actions: list[RequestedAction],
    status_context: StatusContext,
    card_state: CardState,
    compromise: list[CompromiseSignal],
    card_in_possession: str,
    channel_hint: str,
) -> tuple[list[str], list[str], Phase]:
    phase = Phase.Collect
    missing: list[str] = []
    questions: list[str] = []

    if intent == Intent.StatusWhatNext:
        missing = [] if status_context == StatusContext.case_known else ['case_id']
        questions = ['Подскажите номер обращения или уточните, по какой операции нужен статус.']
        phase = Phase.Explain if not missing else Phase.Collect
        return missing, questions, phase

    if intent == Intent.LostStolen:
        questions = [
            'Подтвердите, пожалуйста, что карту нужно заблокировать прямо сейчас.',
            'Были ли уже неизвестные операции после утраты или кражи карты?',
            'Нужен ли перевыпуск карты после блокировки?',
        ]
        if subtype != DisputeSubtype.unknown or RequestedAction.investigate_transaction in actions:
            questions.append('Если вы помните, уточните сумму и примерное время спорной операции.')
        if card_state in {CardState.lost, CardState.stolen} or compromise:
            phase = Phase.Act
            return [], questions, phase
        missing = ['customer_confirm_block']
        return missing, questions, phase

    if intent == Intent.UnblockReissue:
        missing = ['case_id'] if RequestedAction.unblock_card in actions else []
        questions = [
            'Карта уже заблокирована и вы хотите разблокировать ее или нужен перевыпуск?',
            'Подтвердите, пожалуйста, причину перевыпуска или разблокировки.',
        ]
        phase = Phase.Act if not missing else Phase.Collect
        return missing, questions, phase

    if intent == Intent.CardNotWorking:
        channel_hint_value = _enum_value(channel_hint)
        if card_state == CardState.damaged:
            return [], ['Карта физически повреждена или проблема только в конкретном сценарии оплаты?'], Phase.Act
        if channel_hint_value != 'unknown':
            return [], [], Phase.Act
        missing = ['problem_channel_confirm']

        missing = ['problem_channel_confirm']
        questions = [
            'Где именно не работает карта: в магазине, онлайн или в банкомате?',
            'Карта физически повреждена или проблема только в конкретном сценарии оплаты?',
        ]
        return missing, questions, phase

    if intent == Intent.BlockCard:
        missing = ['customer_confirm_block']
        questions = ['Подтвердите, пожалуйста, что вы хотите заблокировать карту сейчас.']
        if RequestedAction.block_card in actions or compromise:
            missing = []
            phase = Phase.Act
        return missing, questions, phase

    if intent == Intent.SuspiciousTransaction:
        missing = ['txn_amount_confirm', 'txn_datetime_confirm']
        if card_in_possession == 'unknown':
            missing.insert(0, 'card_in_possession')
        if RequestedAction.block_card in actions:
            missing.append('customer_confirm_block')

        questions = []
        if card_in_possession == 'unknown':
            questions.append('Подтвердите, пожалуйста, карта сейчас у вас на руках (да/нет).')
        questions.extend([
            'Подтвердите сумму спорной операции.',
            'Подтвердите примерные дату и время спорной операции.',
        ])

        if subtype == DisputeSubtype.recurring_subscription:
            missing.append('merchant_name_confirm')
            questions.append('Подскажите, как называется подписка или сервис, по которому прошло списание?')
        elif subtype == DisputeSubtype.duplicate_charge:
            questions.append('Подтвердите, пожалуйста, что списание прошло дважды по одной и той же операции.')
        elif subtype == DisputeSubtype.reversal_pending:
            questions.append('Подтвердите, пожалуйста, что вы видите холд, резерв или незавершенное списание.')

        return list(dict.fromkeys(missing)), questions, phase

    return [], [], Phase.Collect


def normalize_analyze(history: str, an: AnalyzeV1) -> AnalyzeV1:
    text = history.lower()

    detected_intent = _detect_intent(text)
    detected_subtype = _detect_dispute_subtype(text)
    detected_card_state = _detect_card_state(text)
    detected_card_possession = _detect_card_possession(text)
    detected_channel_hint = _detect_channel_hint(text)
    detected_actions = _detect_requested_actions(text)
    detected_status_context = _detect_status_context(text)
    detected_compromise_signals = _detect_compromise_signals(text)
    detected_customer_claim = _detect_customer_claim(text)

    intent = detected_intent
    subtype = detected_subtype if detected_subtype != DisputeSubtype.unknown else an.facts.dispute_subtype
    if intent == Intent.CardNotWorking and detected_subtype == DisputeSubtype.unknown:
        subtype = DisputeSubtype.unknown

    card_state = detected_card_state if detected_card_state != CardState.unknown else an.facts.card_state
    actions = _unique_enums([*detected_actions, *(an.facts.requested_actions or [])])
    status_context = detected_status_context if detected_status_context != StatusContext.unknown else an.facts.status_context
    compromise_signals = _unique_enums([*detected_compromise_signals, *(an.facts.compromise_signals or [])])

    prev_channel_hint = _coerce_channel_hint(getattr(an.facts, 'channel_hint', ChannelHint.unknown))
    channel_hint = ChannelHint(detected_channel_hint) if detected_channel_hint != 'unknown' else prev_channel_hint
    if intent == Intent.CardNotWorking and detected_channel_hint == 'unknown':
        channel_hint = ChannelHint.unknown

    card_in_possession = an.facts.card_in_possession
    if detected_card_possession != 'unknown':
        card_in_possession = detected_card_possession
    elif card_state == CardState.with_client:
        card_in_possession = 'yes'
    elif card_state in {CardState.lost, CardState.stolen}:
        card_in_possession = 'no'

    missing, questions, phase = _normalized_missing_fields(
        intent,
        subtype,
        actions,
        status_context,
        card_state,
        compromise_signals,
        card_in_possession,
        channel_hint,
    )
    tools = _normalized_tools_for_intent(intent, subtype, actions, compromise_signals)

    risk_level = an.risk_level
    if compromise_signals or intent in {Intent.LostStolen, Intent.BlockCard}:
        risk_level = RiskLevel.high
    elif intent in {Intent.CardNotWorking, Intent.StatusWhatNext, Intent.UnblockReissue, Intent.SuspiciousTransaction} and risk_level == RiskLevel.low:
        risk_level = RiskLevel.medium

    danger_flags = list(an.danger_flags or [])
    if compromise_signals and not any((item.type or '') == 'scam_suspected' for item in danger_flags):
        danger_flags.append(
            DangerFlag(
                type='scam_suspected',
                severity=Severity.high,
                text='Есть признаки мошенничества или социальной инженерии; не запрашивайте коды из SMS/Push и предупредите клиента о риске.',
            )
        )

    if intent == Intent.CardNotWorking:
        summary = 'Клиент сообщает, что карта не работает в одном из сценариев использования.'
        if _enum_value(channel_hint) == 'online':
            summary = 'Клиент сообщает, что карта не работает при онлайн-оплате, при этом карта находится у клиента.'
        elif _enum_value(channel_hint) == 'pos':
            summary = 'Клиент сообщает, что карта не работает при оплате в магазине или через терминал.'
        elif _enum_value(channel_hint) == 'atm':
            summary = 'Клиент сообщает, что карта не работает в банкомате.'
    elif intent == Intent.SuspiciousTransaction:
        if subtype == DisputeSubtype.recurring_subscription:
            summary = 'Клиент сообщает о спорном регулярном списании или подписке и просит проверить ситуацию.'
        elif subtype == DisputeSubtype.duplicate_charge:
            summary = 'Клиент сообщает о возможном двойном списании и просит проверить детали операции.'
        elif subtype == DisputeSubtype.reversal_pending:
            summary = 'Клиент уточняет ситуацию по холду, резерву или незавершенному списанию.'
        else:
            summary = 'Клиент сообщает о спорной или подозрительной операции и просит проверить ситуацию.'
    elif intent == Intent.LostStolen:
        if subtype != DisputeSubtype.unknown or compromise_signals:
            summary = 'Клиент сообщает об утрате или краже карты, а также о возможной спорной операции или признаках компрометации.'
        else:
            summary = 'Клиент сообщает об утрате или краже карты и ожидает безопасные дальнейшие действия.'
    elif intent == Intent.UnblockReissue:
        summary = 'Клиент просит разблокировать карту или оформить перевыпуск.'
    elif intent == Intent.StatusWhatNext:
        summary = 'Клиент уточняет статус обращения или следующий шаг по уже заявленной проблеме.'
    else:
        summary = an.summary_public

    if intent == Intent.SuspiciousTransaction:
        analytics_tags = _merge_tags(list(an.analytics_tags or []), [intent.value.lower(), subtype.value])
        recurring = _merge_tags(list(an.profile_update.recurring_issues or []), [subtype.value] if subtype != DisputeSubtype.unknown else [])
    elif intent == Intent.CardNotWorking:
        base = [intent.value.lower()]
        if _enum_value(channel_hint) != 'unknown':
            base.append(_enum_value(channel_hint))
        filtered_prev = [t for t in (an.analytics_tags or []) if t not in {'suspicious', 'suspicioustransaction'}]
        analytics_tags = _merge_tags(filtered_prev, base)
        recurring = [tag for tag in (an.profile_update.recurring_issues or []) if tag not in {'suspicious'}]
    else:
        analytics_tags = _merge_tags(list(an.analytics_tags or []), [intent.value.lower()])
        recurring = list(an.profile_update.recurring_issues or [])

    prev_customer_claim = str(getattr(an.facts, 'customer_claim', 'unknown') or 'unknown')

    if intent == Intent.SuspiciousTransaction:
        if detected_customer_claim != 'unknown':
            customer_claim = detected_customer_claim
        elif subtype == DisputeSubtype.suspicious:
            customer_claim = 'not_mine'
        elif prev_customer_claim not in {'unknown', 'not_mine'}:
            customer_claim = prev_customer_claim
        else:
            customer_claim = 'unknown'
    else:
        if detected_customer_claim != 'unknown':
            customer_claim = detected_customer_claim
        elif prev_customer_claim not in {'unknown', 'not_mine'}:
            customer_claim = prev_customer_claim
        else:
            customer_claim = 'unknown'
            
    return an.model_copy(
        update={
            'intent': intent,
            'phase': phase,
            'confidence': max(float(an.confidence), 0.88 if intent != Intent.Unknown else 0.42),
            'summary_public': summary,
            'risk_level': risk_level,
            'facts': an.facts.model_copy(
                update={
                    'customer_claim': customer_claim,
                    'card_in_possession': card_in_possession,
                    'channel_hint': channel_hint,
                    'dispute_subtype': subtype,
                    'card_state': card_state,
                    'requested_actions': actions,
                    'status_context': status_context,
                    'compromise_signals': compromise_signals,
                }
            ),
            'profile_update': an.profile_update.model_copy(
                update={
                    'client_card_context': summary,
                    'recurring_issues': recurring,
                    'notes_for_case_file': summary,
                }
            ),
            'missing_fields': missing,
            'next_questions': questions,
            'tools_suggested': tools,
            'danger_flags': danger_flags,
            'analytics_tags': analytics_tags,
        }
    )