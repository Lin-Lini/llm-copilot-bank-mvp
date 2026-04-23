from __future__ import annotations

import json
from typing import Any, Iterable

from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    CardState,
    CaseDossier,
    CaseReadiness,
    CompromiseSignal,
    DangerFlag,
    DossierAction,
    DossierRiskSummary,
    DisputeSubtype,
    Intent,
    Phase,
    ProfileUpdate,
    RequestedAction,
    RiskChecklistItem,
    RiskLevel,
    Severity,
    StatusContext,
)
from libs.common.case_readiness import normalize_intent
from libs.common.json_lists import parse_string_list


def _payload(row: Any) -> dict[str, Any]:
    if getattr(row, 'payload_json', None) is not None:
        value = row.payload_json
        return value if isinstance(value, dict) else {}
    raw = getattr(row, 'payload', None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {'raw': str(raw)}


def _parse_enum(enum_cls, value, default):
    try:
        return enum_cls(value)
    except Exception:
        return default


def _unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = str(item or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _default_risk_level(intent: Intent) -> RiskLevel:
    if intent in {Intent.BlockCard, Intent.LostStolen}:
        return RiskLevel.high
    if intent == Intent.SuspiciousTransaction:
        return RiskLevel.medium
    if intent == Intent.StatusWhatNext:
        return RiskLevel.low
    return RiskLevel.medium


def extract_case_domain_context(case_obj: Any, timeline_rows: Iterable[Any]) -> dict[str, Any]:
    intent = normalize_intent(getattr(case_obj, 'case_type', None))
    dispute_reason = str(getattr(case_obj, 'dispute_reason', '') or '').strip()

    subtype = DisputeSubtype.unknown
    if intent == Intent.SuspiciousTransaction:
        subtype = _parse_enum(DisputeSubtype, dispute_reason, DisputeSubtype.unknown)

    card_state = CardState.unknown
    requested_actions: list[RequestedAction] = []
    status_context = StatusContext.case_known if intent == Intent.StatusWhatNext else StatusContext.unknown
    compromise_signals: list[CompromiseSignal] = []
    risk_level = _default_risk_level(intent)
    summary_public = str(getattr(case_obj, 'summary_public', '') or '').strip()
    analytics_tags: list[str] = []

    for row in timeline_rows:
        payload = _payload(row)
        snap = payload.get('analyze_snapshot') if isinstance(payload.get('analyze_snapshot'), dict) else None
        if snap:
            try:
                an = AnalyzeV1.model_validate(snap)
                intent = an.intent
                subtype = an.facts.dispute_subtype
                card_state = an.facts.card_state
                requested_actions = list(an.facts.requested_actions or [])
                status_context = an.facts.status_context
                compromise_signals = list(an.facts.compromise_signals or [])
                risk_level = an.risk_level
                summary_public = summary_public or an.summary_public
                analytics_tags = list(an.analytics_tags or [])
                continue
            except Exception:
                pass

        domain = payload.get('domain_context') if isinstance(payload.get('domain_context'), dict) else None
        if domain:
            subtype = _parse_enum(DisputeSubtype, domain.get('dispute_subtype'), subtype)
            card_state = _parse_enum(CardState, domain.get('card_state'), card_state)
            status_context = _parse_enum(StatusContext, domain.get('status_context'), status_context)
            requested_actions = [
                _parse_enum(RequestedAction, item, None)
                for item in (domain.get('requested_actions') or [])
            ]
            requested_actions = [item for item in requested_actions if item is not None]
            compromise_signals = [
                _parse_enum(CompromiseSignal, item, None)
                for item in (domain.get('compromise_signals') or [])
            ]
            compromise_signals = [item for item in compromise_signals if item is not None]

    return {
        'intent': intent,
        'dispute_subtype': subtype,
        'card_state': card_state,
        'requested_actions': requested_actions,
        'status_context': status_context,
        'compromise_signals': compromise_signals,
        'risk_level': risk_level,
        'summary_public': summary_public,
        'analytics_tags': analytics_tags,
    }


def _standard_checklist() -> list[RiskChecklistItem]:
    return [
        RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.'),
        RiskChecklistItem(id='no_pin', severity=Severity.high, text='Не запрашивать ПИН-код.'),
        RiskChecklistItem(id='no_sms_codes', severity=Severity.high, text='Не запрашивать одноразовые коды из SMS/Push.'),
        RiskChecklistItem(id='no_full_pan', severity=Severity.high, text='Не запрашивать полный номер карты.'),
        RiskChecklistItem(id='no_refund_promise', severity=Severity.medium, text='Не обещать возврат средств; исход зависит от рассмотрения.'),
    ]


def _danger_flags_from_context(intent: Intent, ctx: dict[str, Any]) -> list[DangerFlag]:
    out: list[DangerFlag] = []
    signals: list[CompromiseSignal] = list(ctx['compromise_signals'])
    subtype: DisputeSubtype = ctx['dispute_subtype']
    card_state: CardState = ctx['card_state']

    mapping = {
        CompromiseSignal.sms_code_shared: 'Клиент сообщил код из SMS/Push.',
        CompromiseSignal.safe_account: 'Клиент упоминал перевод на безопасный счет.',
        CompromiseSignal.remote_access: 'Клиент упоминал удаленный доступ или установку стороннего приложения.',
        CompromiseSignal.spoofed_call: 'Есть признаки звонка с подменного номера или давления на клиента.',
        CompromiseSignal.cvv_shared: 'Есть риск компрометации реквизитов карты.',
    }
    for signal in signals:
        text = mapping.get(signal)
        if text:
            out.append(DangerFlag(type=signal.value, severity=Severity.high, text=text))

    if subtype == DisputeSubtype.recurring_subscription:
        out.append(DangerFlag(type='subscription_dispute', severity=Severity.medium, text='Спор связан с регулярным списанием или подпиской.'))
    elif subtype == DisputeSubtype.duplicate_charge:
        out.append(DangerFlag(type='duplicate_charge', severity=Severity.medium, text='Есть признаки двойного списания.'))
    elif subtype == DisputeSubtype.reversal_pending:
        out.append(DangerFlag(type='reversal_pending', severity=Severity.low, text='Вероятен холд или резерв вместо финального списания.'))

    if intent in {Intent.LostStolen, Intent.BlockCard} or card_state in {CardState.lost, CardState.stolen}:
        out.append(DangerFlag(type='card_compromise', severity=Severity.high, text='Есть риск утраты, кражи или компрометации карты.'))

    uniq: list[DangerFlag] = []
    seen: set[tuple[str, str]] = set()
    for item in out:
        key = (item.type, item.text)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def build_analyze_from_case_context(case_obj: Any, timeline_rows: Iterable[Any]) -> AnalyzeV1:
    ctx = extract_case_domain_context(case_obj, timeline_rows)
    intent: Intent = ctx['intent']
    confirmed = parse_string_list(getattr(case_obj, 'facts_confirmed_json', None))
    pending = parse_string_list(getattr(case_obj, 'facts_pending_json', None))
    card_state: CardState = ctx['card_state']

    card_in_possession = 'unknown'
    if card_state == CardState.with_client or 'card_in_possession' in confirmed:
        card_in_possession = 'yes'
    elif card_state in {CardState.lost, CardState.stolen}:
        card_in_possession = 'no'

    phase = Phase.Collect if pending else (Phase.Explain if intent == Intent.StatusWhatNext else Phase.Act)

    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=phase,
        confidence=0.95,
        summary_public=ctx['summary_public'] or str(getattr(case_obj, 'summary_public', '') or '').strip() or f'Кейс по сценарию {intent.value}.',
        risk_level=ctx['risk_level'],
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint='unknown',
            customer_claim='unknown',
            card_in_possession=card_in_possession,
            delivery_pref=None,
            previous_actions=[],
            dispute_subtype=ctx['dispute_subtype'],
            card_state=ctx['card_state'],
            requested_actions=list(ctx['requested_actions']),
            status_context=ctx['status_context'],
            compromise_signals=list(ctx['compromise_signals']),
        ),
        profile_update=ProfileUpdate(
            client_card_context=ctx['summary_public'] or '',
            recurring_issues=list(ctx['analytics_tags']),
            notes_for_case_file=str(getattr(case_obj, 'decision_summary', '') or '').strip(),
        ),
        missing_fields=pending,
        next_questions=[],
        tools_suggested=[],
        danger_flags=_danger_flags_from_context(intent, ctx),
        risk_checklist=_standard_checklist(),
        analytics_tags=list(ctx['analytics_tags']),
    )


def _humanize_fact_name(name: str, *, ctx: dict[str, Any], pending: bool) -> str:
    prefix = 'Подтвердить' if pending else ''
    subtype: DisputeSubtype = ctx['dispute_subtype']
    card_state: CardState = ctx['card_state']

    mapping = {
        'card_in_possession': 'что карта находится у клиента' if pending else 'Карта находится у клиента',
        'txn_amount_confirm': 'сумму спорной операции' if pending else 'Подтверждена сумма спорной операции',
        'txn_datetime_confirm': 'дату и время спорной операции' if pending else 'Подтверждены дата и время спорной операции',
        'merchant_name_confirm': 'название сервиса или подписки' if pending else 'Уточнено название сервиса или подписки',
        'customer_confirm_block': 'согласие клиента на блокировку карты' if pending else 'Клиент подтвердил блокировку карты',
        'problem_channel_confirm': 'где именно не работает карта' if pending else 'Уточнен канал, где не работает карта',
        'case_id': 'номер обращения' if pending else 'Подтвержден номер обращения',
        'dispute_subtype': 'тип спора' if pending else 'Уточнен тип спора',
        'status_context': 'контекст статуса обращения' if pending else 'Контекст статуса обращения подтвержден',
        'compromise_signals': 'наличие признаков компрометации' if pending else 'Подтверждены признаки компрометации',
        'requested_actions': 'запрошенное действие клиента' if pending else 'Уточнено запрошенное действие клиента',
    }
    if name in mapping:
        text = mapping[name]
        return f'{prefix} {text}'.strip().capitalize() if pending else text

    if name == 'card_state':
        if card_state == CardState.lost:
            return 'Подтвердить факт утраты карты' if pending else 'Подтвержден факт утраты карты'
        if card_state == CardState.stolen:
            return 'Подтвердить факт кражи карты' if pending else 'Подтвержден факт кражи карты'
        if card_state == CardState.damaged:
            return 'Подтвердить повреждение карты' if pending else 'Подтверждено повреждение карты'
        return 'Подтвердить состояние карты' if pending else 'Уточнено состояние карты'

    if name == 'dispute_reason' and subtype != DisputeSubtype.unknown:
        return f'Подтвердить подтип спора: {subtype.value}' if pending else f'Подтип спора: {subtype.value}'

    return name.replace('_', ' ').capitalize()


def _risk_summary(intent: Intent, ctx: dict[str, Any], case_obj: Any, timeline_rows: Iterable[Any]) -> DossierRiskSummary:
    signals: list[CompromiseSignal] = list(ctx['compromise_signals'])
    subtype: DisputeSubtype = ctx['dispute_subtype']
    card_state: CardState = ctx['card_state']

    risk_level = ctx['risk_level']
    if signals or card_state in {CardState.lost, CardState.stolen} or intent in {Intent.BlockCard, Intent.LostStolen}:
        risk_level = RiskLevel.high
    elif intent == Intent.SuspiciousTransaction and subtype in {DisputeSubtype.recurring_subscription, DisputeSubtype.duplicate_charge}:
        risk_level = RiskLevel.medium
    elif intent == Intent.StatusWhatNext:
        risk_level = RiskLevel.low

    danger_flags = [item.text for item in _danger_flags_from_context(intent, ctx)]
    security_notes: list[str] = []

    if intent == Intent.SuspiciousTransaction:
        if subtype == DisputeSubtype.recurring_subscription:
            security_notes.append('Не предлагать блокировку карты как первое действие без отдельного подтверждения клиента.')
        elif subtype == DisputeSubtype.duplicate_charge:
            security_notes.append('Сначала сверить повторное списание и только потом переходить к следующим действиям.')
        elif subtype == DisputeSubtype.reversal_pending:
            security_notes.append('Уточнить, идет ли речь о холде или резерве, прежде чем обещать спор или возврат.')
        else:
            security_notes.append('Сверять подтвержденные параметры операции перед следующим действием.')
    if intent in {Intent.BlockCard, Intent.LostStolen}:
        security_notes.append('Приоритетный шаг — блокировка карты и фиксация кейса.')
    if intent == Intent.UnblockReissue:
        security_notes.append('Не обещать разблокировку без подтвержденного контекста блокировки и статуса кейса.')
    if intent == Intent.CardNotWorking:
        security_notes.append('Сначала исключить лимиты и настройки, затем переходить к перевыпуску.')
    if intent == Intent.StatusWhatNext:
        security_notes.append('Статус сообщать только по подтвержденным данным системы.')

    if str(getattr(case_obj, 'priority', '')).lower() == 'high':
        danger_flags.append('Кейс отмечен высоким приоритетом.')

    return DossierRiskSummary(
        risk_level=risk_level,
        danger_flags=_unique(danger_flags),
        security_notes=_unique(security_notes),
    )


def _action_summary(kind: str, payload: dict[str, Any], ctx: dict[str, Any]) -> str:
    if kind == 'case_created':
        subtype: DisputeSubtype = ctx['dispute_subtype']
        if ctx['intent'] == Intent.SuspiciousTransaction and subtype != DisputeSubtype.unknown:
            return f'Обращение зарегистрировано по подтипу спора: {subtype.value}.'
        if ctx['intent'] == Intent.LostStolen and ctx['card_state'] in {CardState.lost, CardState.stolen}:
            return 'Обращение зарегистрировано по сценарию утраты или кражи карты.'
        return 'Обращение зарегистрировано в системе.'

    if kind == 'profile_confirmed':
        stored = payload.get('stored')
        return f'Подтверждены поля кейса: {stored}.' if stored is not None else 'Подтверждены поля кейса.'

    if kind == 'tool_result':
        tool = payload.get('tool')
        result = payload.get('result') or {}
        if tool == 'create_case':
            return 'Кейс создан и привязан к текущему обращению.'
        if tool == 'get_transactions':
            subtype: DisputeSubtype = ctx['dispute_subtype']
            if subtype == DisputeSubtype.duplicate_charge:
                return 'Получен список операций для проверки двойного списания.'
            if subtype == DisputeSubtype.recurring_subscription:
                return 'Получен список операций для проверки регулярного списания.'
            if subtype == DisputeSubtype.reversal_pending:
                return 'Получен список операций для проверки холда или резерва.'
            return 'Получен список операций для сверки.'
        if tool == 'get_case_status':
            return 'Получен подтвержденный статус кейса.'
        if tool == 'block_card':
            return 'Выполнена блокировка карты.'
        if tool == 'reissue_card':
            eta = result.get('eta_days')
            return f'Оформлен перевыпуск карты, ориентировочный срок: {eta} дн.' if eta is not None else 'Оформлен перевыпуск карты.'
        if tool == 'unblock_card':
            return 'Выполнена разблокировка карты.'
        if tool == 'get_card_limits':
            return 'Получены лимиты и настройки карты.'
        if tool == 'toggle_online_payments':
            return 'Изменены настройки онлайн-платежей.'
        return f'Получен результат инструмента: {tool}.'

    if kind == 'case_updated':
        fields = payload.get('changed_fields') or []
        if isinstance(fields, list) and fields:
            return f'Кейс обновлен: {", ".join(fields)}.'
        if 'status' in payload:
            return 'Обновлен статус кейса.'
        return 'Кейс обновлен.'

    return f'Зафиксировано событие: {kind}.'


def _actions_taken(timeline_rows: Iterable[Any], ctx: dict[str, Any]) -> list[DossierAction]:
    out: list[DossierAction] = []
    for row in timeline_rows:
        kind = str(getattr(row, 'kind', ''))
        payload = _payload(row)
        created_at = getattr(row, 'created_at', None)
        out.append(
            DossierAction(
                kind=kind,
                summary=_action_summary(kind, payload, ctx),
                created_at=created_at.isoformat() if created_at else '',
            )
        )
    return out


def _operator_safe_context(*, ctx: dict[str, Any], current_status: str, confirmed_facts: list[str], pending_facts: list[str], next_expected_step: str) -> str:
    intent: Intent = ctx['intent']
    subtype: DisputeSubtype = ctx['dispute_subtype']
    card_state: CardState = ctx['card_state']

    scenario = intent.value
    if intent == Intent.SuspiciousTransaction and subtype != DisputeSubtype.unknown:
        scenario = f'{intent.value}:{subtype.value}'
    if intent in {Intent.LostStolen, Intent.UnblockReissue, Intent.CardNotWorking} and card_state != CardState.unknown:
        scenario = f'{intent.value}:{card_state.value}'

    confirmed = ', '.join(confirmed_facts) if confirmed_facts else 'нет подтвержденных фактов'
    pending = ', '.join(pending_facts) if pending_facts else 'нет незакрытых обязательных полей'
    return (
        f'Сценарий: {scenario}. '
        f'Статус кейса: {current_status}. '
        f'Подтвержденные факты: {confirmed}. '
        f'Ожидающие подтверждения: {pending}. '
        f'Следующий ожидаемый шаг: {next_expected_step}'
    )


def build_case_dossier(case_obj: Any, *, readiness: CaseReadiness, timeline_rows: Iterable[Any]) -> CaseDossier:
    ctx = extract_case_domain_context(case_obj, timeline_rows)
    intent: Intent = ctx['intent']

    confirmed_raw = parse_string_list(getattr(case_obj, 'facts_confirmed_json', None))
    pending_raw = parse_string_list(getattr(case_obj, 'facts_pending_json', None))
    confirmed_facts = _unique(_humanize_fact_name(name, ctx=ctx, pending=False) for name in confirmed_raw)
    pending_facts = _unique(_humanize_fact_name(name, ctx=ctx, pending=True) for name in pending_raw)

    current_status = str(getattr(case_obj, 'status', '') or 'open')
    client_problem_summary = (
        str(getattr(case_obj, 'summary_public', '') or '').strip()
        or ctx['summary_public']
        or str(getattr(case_obj, 'dispute_reason', '') or '').strip()
        or f'Обращение по сценарию {intent.value}.'
    )

    risk_summary = _risk_summary(intent, ctx, case_obj, timeline_rows)
    actions_taken = _actions_taken(timeline_rows, ctx)
    next_expected_step = readiness.next_action
    operator_safe_context = _operator_safe_context(
        ctx=ctx,
        current_status=current_status,
        confirmed_facts=confirmed_facts,
        pending_facts=pending_facts,
        next_expected_step=next_expected_step,
    )

    return CaseDossier(
        case_id=str(getattr(case_obj, 'id')),
        intent=intent,
        client_problem_summary=client_problem_summary,
        confirmed_facts=confirmed_facts,
        pending_facts=pending_facts,
        risk_summary=risk_summary,
        actions_taken=actions_taken,
        current_status=current_status,
        next_expected_step=next_expected_step,
        operator_safe_context=operator_safe_context,
    )