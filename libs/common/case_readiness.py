from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts.schemas import (
    CaseReadiness,
    Intent,
    MissingFieldMeta,
    Phase,
    ReadinessStatus,
    ReadinessToolState,
    Severity,
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
    'customer_confirm_block': _FieldRule(
        label='Подтверждение на блокировку карты',
        why_needed='Без подтверждения нельзя безопасно инициировать блокировку, если сценарий не допускает high-risk path.',
        severity=Severity.medium,
        blocks_tools=(ToolName.block_card,),
        confirmable=True,
        suggested_question='Подтвердите, пожалуйста, что вы хотите заблокировать карту сейчас.',
        weight=10,
    ),
    'case_id': _FieldRule(
        label='Номер обращения',
        why_needed='Номер обращения нужен для проверки статуса и следующего шага без догадок.',
        severity=Severity.high,
        blocks_tools=(ToolName.get_case_status,),
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


def required_pending_fields(intent: Intent | str | None) -> list[str]:
    it = normalize_intent(intent)
    if it == Intent.SuspiciousTransaction:
        return ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'customer_confirm_block']
    if it in {Intent.BlockCard, Intent.LostStolen}:
        return ['customer_confirm_block']
    if it == Intent.StatusWhatNext:
        return ['case_id']
    return []


def build_missing_field_meta(intent: Intent | str | None, missing_fields: Iterable[str] | None) -> list[MissingFieldMeta]:
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

        out.append(
            MissingFieldMeta(
                field_name=name,
                label=rule.label,
                why_needed=rule.why_needed,
                severity=rule.severity,
                blocks_tools=list(rule.blocks_tools),
                confirmable=rule.confirmable,
                suggested_question=rule.suggested_question,
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


def _suggest_next_action(
    *,
    status: ReadinessStatus,
    missing_meta: list[MissingFieldMeta],
    ready_tools: list[ReadinessToolState],
    intent: Intent,
    case_status: str | None,
) -> str:
    if status == ReadinessStatus.completed:
        return 'Обращение завершено; можно использовать итоговое досье и статус для дальнейшей коммуникации.'

    if missing_meta:
        top = missing_meta[0]
        return top.suggested_question or f'Уточнить поле: {top.label}.'

    if ready_tools:
        preferred = {
            Intent.SuspiciousTransaction: ToolName.get_transactions,
            Intent.BlockCard: ToolName.block_card,
            Intent.LostStolen: ToolName.block_card,
            Intent.StatusWhatNext: ToolName.get_case_status,
        }.get(intent)

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
        meta = build_missing_field_meta(it, raw_missing)
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

def infer_case_phase(intent: Intent | str | None, missing_fields: Iterable[str] | None, case_status: str | None = None) -> Phase:
    if (case_status or '').lower() in {'closed', 'resolved', 'done'}:
        return Phase.Explain
    if list(missing_fields or []):
        return Phase.Collect
    if normalize_intent(intent) == Intent.StatusWhatNext:
        return Phase.Explain
    return Phase.Act