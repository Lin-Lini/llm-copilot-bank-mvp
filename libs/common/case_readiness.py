from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    CaseReadiness,
    DisputeSubtype,
    Intent,
    MissingFieldMeta,
    Phase,
    ReadinessStatus,
    ReadinessToolState,
    RequestedAction,
    Severity,
    StatusContext,
    ToolName,
    ToolUI,
)


@dataclass(frozen=True)
class _FieldRule:
    label: str
    why_needed: str
    severity: Severity
    blocks_tools: tuple[ToolName, ...]
    confirmable: bool
    suggested_question: str
    weight: int


_FIELD_RULES: dict[str, _FieldRule] = {
    'card_in_possession': _FieldRule(
        label='Карта у клиента',
        why_needed='Нужно понять, идет ли речь о спорной операции при наличии карты или о компрометации/утрате.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_transactions, ToolName.create_case),
        confirmable=True,
        suggested_question='Подтвердите, пожалуйста, карта сейчас у вас на руках?',
        weight=30,
    ),
    'txn_amount_confirm': _FieldRule(
        label='Подтвержденная сумма операции',
        why_needed='Сумма нужна для точной сверки операции и корректного оформления обращения.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_transactions, ToolName.create_case),
        confirmable=True,
        suggested_question='Подтвердите сумму спорной операции.',
        weight=25,
    ),
    'txn_datetime_confirm': _FieldRule(
        label='Подтвержденные дата и время операции',
        why_needed='Дата и время нужны для поиска операции и исключения ложных совпадений.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_transactions, ToolName.create_case),
        confirmable=True,
        suggested_question='Подтвердите примерные дату и время спорной операции.',
        weight=25,
    ),
    'merchant_name_confirm': _FieldRule(
        label='Название сервиса или подписки',
        why_needed='Название сервиса помогает проверить регулярное списание и корректно классифицировать спор.',
        severity=Severity.medium,
        blocks_tools=(ToolName.get_transactions, ToolName.create_case),
        confirmable=True,
        suggested_question='Подскажите, как называется сервис или подписка, по которой прошло списание?',
        weight=12,
    ),
    'customer_confirm_block': _FieldRule(
        label='Подтверждение на блокировку карты',
        why_needed='Без подтверждения нельзя безопасно инициировать блокировку, если сценарий не допускает high-risk path.',
        severity=Severity.medium,
        blocks_tools=(ToolName.block_card,),
        confirmable=True,
        suggested_question='Подтвердите, пожалуйста, что вы хотите заблокировать карту сейчас.',
        weight=10,
    ),
    'problem_channel_confirm': _FieldRule(
        label='Канал, где не работает карта',
        why_needed='Нужно различить проблему магазина, онлайн-оплаты, банкомата или повреждения карты.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_card_limits, ToolName.toggle_online_payments, ToolName.reissue_card),
        confirmable=True,
        suggested_question='Подскажите, где именно не работает карта: в магазине, онлайн или в банкомате?',
        weight=22,
    ),
    'case_id': _FieldRule(
        label='Номер обращения',
        why_needed='Номер обращения нужен для проверки статуса и следующего шага без догадок.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_case_status, ToolName.unblock_card),
        confirmable=True,
        suggested_question='Подскажите номер обращения, чтобы я мог проверить статус.',
        weight=35,
    ),
}


def normalize_intent(value: Intent | str | None) -> Intent:
    if isinstance(value, Intent):
        return value
    try:
        return Intent(str(value))
    except Exception:
        return Intent.Unknown


def required_pending_fields(intent: Intent | str | None, analyze: AnalyzeV1 | None = None) -> list[str]:
    it = normalize_intent(intent)
    if analyze is None:
        if it == Intent.SuspiciousTransaction:
            return ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block']
        if it in {Intent.BlockCard, Intent.LostStolen}:
            return ['customer_confirm_block']
        if it == Intent.StatusWhatNext:
            return ['case_id']
        if it == Intent.CardNotWorking:
            return ['problem_channel_confirm']
        return []

    subtype = analyze.facts.dispute_subtype
    actions = set(analyze.facts.requested_actions or [])
    status_context = analyze.facts.status_context
    card_state = analyze.facts.card_state
    card_in_possession = analyze.facts.card_in_possession
    channel_hint = getattr(analyze.facts.channel_hint, 'value', analyze.facts.channel_hint)
    compromise = set(analyze.facts.compromise_signals or [])

    if it == Intent.StatusWhatNext:
        return [] if status_context in {StatusContext.case_known, StatusContext.waiting_review, StatusContext.resolved} else ['case_id']

    if it == Intent.LostStolen:
        if card_state in {CardState.lost, CardState.stolen} or RequestedAction.block_card in actions or compromise:
            return []
        return ['customer_confirm_block']

    if it == Intent.BlockCard:
        return [] if RequestedAction.block_card in actions or compromise else ['customer_confirm_block']

    if it == Intent.UnblockReissue:
        if RequestedAction.unblock_card in actions:
            return [] if status_context in {StatusContext.case_known, StatusContext.waiting_review, StatusContext.resolved} else ['case_id']
        if RequestedAction.reissue_card in actions or card_state in {CardState.lost, CardState.stolen, CardState.damaged}:
            return []
        return []

    if it == Intent.CardNotWorking:
        if card_state == CardState.damaged:
            return []
        if channel_hint in {'online', 'pos', 'atm'}:
            return []
        return ['problem_channel_confirm']

    if it == Intent.SuspiciousTransaction:
        missing = ['txn_amount_confirm', 'txn_datetime_confirm']

        possession_known = (
            card_in_possession in {'yes', 'no'}
            or card_state in {CardState.with_client, CardState.lost, CardState.stolen}
        )
        if not possession_known:
            missing.insert(0, 'card_in_possession')

        if subtype == DisputeSubtype.recurring_subscription:
            missing.append('merchant_name_confirm')

        if RequestedAction.block_card in actions:
            missing.append('customer_confirm_block')

        return list(dict.fromkeys(missing))

    return []

    
def build_missing_field_meta(
    intent: Intent | str | None,
    missing_fields: Iterable[str] | None,
    analyze: AnalyzeV1 | None = None,
) -> list[MissingFieldMeta]:
    _ = normalize_intent(intent)
    out: list[MissingFieldMeta] = []
    for name in missing_fields or []:
        rule = _FIELD_RULES.get(name)
        if rule is None:
            out.append(
                MissingFieldMeta(
                    field_name=name,
                    label=name.replace('_', ' ').capitalize(),
                    why_needed='Поле необходимо для безопасного продолжения сценария и фиксации подтвержденных фактов.',
                    severity=Severity.medium,
                    blocks_tools=[],
                    confirmable=True,
                    suggested_question=None,
                )
            )
            continue

        why_needed = rule.why_needed
        suggested_question = rule.suggested_question
        if name == 'customer_confirm_block' and analyze is not None and analyze.facts.card_state in {CardState.lost, CardState.stolen}:
            why_needed = 'Подтверждение полезно для коммуникации, но при явной утрате или краже карта считается высокорисковой уже по описанию клиента.'
        if name == 'merchant_name_confirm' and analyze is not None and analyze.facts.dispute_subtype == DisputeSubtype.recurring_subscription:
            why_needed = 'Название подписки или сервиса помогает отличить регулярное списание от неизвестной операции и корректно оформить кейс.'
        if name == 'case_id' and analyze is not None and analyze.facts.status_context == StatusContext.case_unknown:
            why_needed = 'Без номера обращения нельзя надежно сообщить статус и следующий шаг.'
            suggested_question = 'Подскажите номер обращения или уточните, по какой операции вы хотите узнать статус.'

        out.append(
            MissingFieldMeta(
                field_name=name,
                label=rule.label,
                why_needed=why_needed,
                severity=rule.severity,
                blocks_tools=list(rule.blocks_tools),
                confirmable=rule.confirmable,
                suggested_question=suggested_question,
            )
        )
    return out


def _field_weight(field_name: str) -> int:
    rule = _FIELD_RULES.get(field_name)
    return rule.weight if rule else 12


def _tool_states(tools: list[ToolUI]) -> tuple[list[ReadinessToolState], list[ReadinessToolState]]:
    ready: list[ReadinessToolState] = []
    blocked: list[ReadinessToolState] = []
    for tool in tools:
        item = ReadinessToolState(tool=tool.tool, ready=tool.enabled, reason=tool.reason)
        if tool.enabled:
            ready.append(item)
        else:
            blocked.append(item)
    return ready, blocked


def _preferred_tool(intent: Intent, analyze: AnalyzeV1 | None) -> ToolName | None:
    if intent == Intent.StatusWhatNext:
        return ToolName.get_case_status
    if intent in {Intent.BlockCard, Intent.LostStolen}:
        return ToolName.block_card
    if intent == Intent.UnblockReissue:
        if analyze and RequestedAction.unblock_card in set(analyze.facts.requested_actions or []):
            return ToolName.unblock_card
        return ToolName.reissue_card
    if intent == Intent.CardNotWorking:
        if analyze and analyze.facts.card_state == CardState.damaged:
            return ToolName.reissue_card
        if analyze and analyze.facts.channel_hint == 'online':
            return ToolName.toggle_online_payments
        return ToolName.get_card_limits
    if intent == Intent.SuspiciousTransaction:
        if analyze and analyze.facts.dispute_subtype in {DisputeSubtype.recurring_subscription, DisputeSubtype.duplicate_charge, DisputeSubtype.reversal_pending}:
            return ToolName.get_transactions
        return ToolName.create_case
    return None


def _suggest_next_action(
    *,
    status: ReadinessStatus,
    missing_meta: list[MissingFieldMeta],
    ready_tools: list[ReadinessToolState],
    intent: Intent,
    case_status: str | None,
    analyze: AnalyzeV1 | None,
) -> str:
    if status == ReadinessStatus.completed:
        return 'Обращение завершено; можно использовать итоговое досье и статус для дальнейшей коммуникации.'

    if missing_meta:
        top = missing_meta[0]
        return top.suggested_question or f'Уточнить поле: {top.label}.'

    if ready_tools:
        preferred = _preferred_tool(intent, analyze)
        chosen = next((item for item in ready_tools if item.tool == preferred), ready_tools[0])
        return f'Следующее действие: {chosen.tool.value}.'

    if case_status == 'open':
        return 'Кейс открыт, ожидается следующий подтвержденный шаг оператора или системы.'

    return 'Нужно уточнить состояние кейса и определить следующий шаг.'


def build_readiness(
    *,
    intent: Intent | str | None,
    missing_fields: Iterable[str] | None,
    tools: list[ToolUI] | None = None,
    case_status: str | None = None,
    analyze: AnalyzeV1 | None = None,
) -> CaseReadiness:
    it = normalize_intent(intent)
    raw_missing = list(dict.fromkeys(missing_fields or []))
    ready_tools, blocked_tools = _tool_states(list(tools or []))

    closed_statuses = {'closed', 'resolved', 'done'}
    is_terminal = (case_status or '').lower() in closed_statuses

    if is_terminal:
        meta: list[MissingFieldMeta] = []
        blockers: list[str] = []
        blocked_tools = []
        score = 100
        status = ReadinessStatus.completed
    else:
        meta = build_missing_field_meta(it, raw_missing, analyze)
        score = max(0, 100 - sum(_field_weight(item.field_name) for item in meta))
        blockers = [item.field_name for item in meta if item.severity == Severity.high]

        if blockers:
            status = ReadinessStatus.needs_info
        elif ready_tools:
            status = ReadinessStatus.ready
        else:
            status = ReadinessStatus.in_progress

    next_action = _suggest_next_action(
        status=status,
        missing_meta=meta,
        ready_tools=ready_tools,
        intent=it,
        case_status=case_status,
        analyze=analyze,
    )

    return CaseReadiness(
        score=score,
        status=status,
        blockers=blockers,
        missing_fields=meta,
        ready_tools=ready_tools,
        blocked_tools=blocked_tools,
        next_action=next_action,
    )


def infer_case_phase(intent: Intent | str | None, missing_fields: Iterable[str] | None, case_status: str | None = None, analyze: AnalyzeV1 | None = None) -> Phase:
    if (case_status or '').lower() in {'closed', 'resolved', 'done'}:
        return Phase.Explain
    if list(missing_fields or []):
        return Phase.Collect
    if normalize_intent(intent) == Intent.StatusWhatNext:
        return Phase.Explain
    if analyze and normalize_intent(intent) == Intent.CardNotWorking and analyze.facts.card_state == CardState.damaged:
        return Phase.Act
    return Phase.Act