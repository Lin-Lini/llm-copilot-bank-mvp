from __future__ import annotations

import json
from typing import Any, Iterable

from contracts.schemas import CaseDossier, CaseReadiness, DossierAction, DossierRiskSummary, Intent, RiskLevel
from libs.common.case_readiness import normalize_intent
from libs.common.json_lists import parse_string_list

def _payload(row: Any) -> dict[str, Any]:
    if getattr(row, 'payload_json', None) is not None:
        return row.payload_json
    raw = getattr(row, 'payload', None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {'raw': str(raw)}


def _risk_summary(intent: Intent, case_obj: Any, timeline_rows: Iterable[Any]) -> DossierRiskSummary:
    level = RiskLevel.low
    if intent in {Intent.SuspiciousTransaction, Intent.BlockCard, Intent.LostStolen}:
        level = RiskLevel.high

    danger_flags: list[str] = []
    security_notes: list[str] = []

    if intent == Intent.SuspiciousTransaction:
        danger_flags.append('Возможен сценарий спорной или мошеннической операции.')
        security_notes.extend(
            [
                'Не запрашивать CVV/CVC, ПИН и коды из SMS/Push.',
                'Проверять статус карты и подтвержденные параметры операции перед следующим действием.',
            ]
        )
    elif intent in {Intent.BlockCard, Intent.LostStolen}:
        danger_flags.append('Имеется риск компрометации или утраты карты.')
        security_notes.extend(
            [
                'При подтверждении сценария приоритетным действием является блокировка карты.',
                'Не передавать клиенту неподтвержденные обещания о результате рассмотрения.',
            ]
        )
    elif intent == Intent.StatusWhatNext:
        security_notes.append('Статус кейса сообщать только по подтвержденным данным системы.')

    if str(getattr(case_obj, 'priority', '')).lower() == 'high':
        danger_flags.append('Кейс отмечен высоким приоритетом.')

    seen_tools: set[str] = set()
    for row in timeline_rows:
        if getattr(row, 'kind', '') == 'tool_result':
            tool = str(_payload(row).get('tool') or '')
            if tool and tool not in seen_tools:
                seen_tools.add(tool)
                security_notes.append(f'Зафиксирован подтвержденный результат инструмента: {tool}.')

    return DossierRiskSummary(
        risk_level=level,
        danger_flags=list(dict.fromkeys(danger_flags)),
        security_notes=list(dict.fromkeys(security_notes)),
    )


def _action_summary(kind: str, payload: dict[str, Any]) -> str:
    if kind == 'case_created':
        return 'Обращение зарегистрировано в системе.'
    if kind == 'profile_confirmed':
        stored = payload.get('stored')
        return f'Подтверждены поля кейса: {stored}.' if stored is not None else 'Подтверждены поля кейса.'
    if kind == 'tool_result':
        tool = payload.get('tool')
        if tool == 'create_case':
            return 'Создан кейс по обращению.'
        if tool == 'get_transactions':
            return 'Получен список операций для сверки.'
        if tool == 'get_case_status':
            return 'Получен подтвержденный статус кейса.'
        if tool == 'block_card':
            return 'Выполнена блокировка карты.'
        return f'Получен результат инструмента: {tool}.'
    if kind == 'case_updated':
        if 'status' in payload:
            return 'Обновлен статус кейса.'
        if 'facts_confirmed' in payload or 'facts_pending' in payload:
            return 'Обновлен состав подтвержденных и ожидающих фактов.'
        return 'Кейс обновлен.'
    return f'Зафиксировано событие: {kind}.'


def _actions_taken(timeline_rows: Iterable[Any]) -> list[DossierAction]:
    out: list[DossierAction] = []
    for row in timeline_rows:
        kind = str(getattr(row, 'kind', ''))
        payload = _payload(row)
        created_at = getattr(row, 'created_at', None)
        out.append(
            DossierAction(
                kind=kind,
                summary=_action_summary(kind, payload),
                created_at=created_at.isoformat() if created_at else '',
            )
        )
    return out


def _operator_safe_context(
    *,
    current_status: str,
    confirmed_facts: list[str],
    pending_facts: list[str],
    next_expected_step: str,
) -> str:
    confirmed = ', '.join(confirmed_facts) if confirmed_facts else 'нет подтвержденных фактов'
    pending = ', '.join(pending_facts) if pending_facts else 'нет незакрытых обязательных полей'
    return (
        f'Статус кейса: {current_status}. '
        f'Подтвержденные факты: {confirmed}. '
        f'Ожидающие подтверждения: {pending}. '
        f'Следующий ожидаемый шаг: {next_expected_step}'
    )


def build_case_dossier(
    case_obj: Any,
    *,
    readiness: CaseReadiness,
    timeline_rows: Iterable[Any],
) -> CaseDossier:
    intent = normalize_intent(getattr(case_obj, 'case_type', None))
    confirmed_facts = parse_string_list(getattr(case_obj, 'facts_confirmed_json', None))
    pending_facts = parse_string_list(getattr(case_obj, 'facts_pending_json', None))
    current_status = str(getattr(case_obj, 'status', '') or 'open')

    client_problem_summary = (
        str(getattr(case_obj, 'summary_public', '') or '').strip()
        or str(getattr(case_obj, 'dispute_reason', '') or '').strip()
        or f'Обращение по сценарию {intent.value}.'
    )

    risk_summary = _risk_summary(intent, case_obj, timeline_rows)
    actions_taken = _actions_taken(timeline_rows)
    next_expected_step = readiness.next_action
    operator_safe_context = _operator_safe_context(
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