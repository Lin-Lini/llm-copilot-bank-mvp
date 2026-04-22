from types import SimpleNamespace
from datetime import datetime, timezone

from contracts.schemas import Intent, Phase
from libs.common.case_dossier import build_case_dossier
from libs.common.case_readiness import build_readiness
from libs.common.state_engine import resolve_tools


def _timeline_row(idx: int, kind: str, payload: dict):
    return SimpleNamespace(
        id=idx,
        kind=kind,
        payload=payload,
        payload_json=payload,
        created_at=datetime(2026, 4, 21, 1, idx, 0, tzinfo=timezone.utc),
    )


def test_case_dossier_contains_actions_and_next_step():
    case_obj = SimpleNamespace(
        id='case-1',
        case_type=Intent.SuspiciousTransaction.value,
        summary_public='Клиент сообщает о подозрительном списании.',
        dispute_reason='SuspiciousTransaction',
        facts_confirmed_json='["card_in_possession"]',
        facts_pending_json='["txn_amount_confirm","txn_datetime_confirm"]',
        priority='high',
        status='open',
    )

    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Collect,
        missing_fields=['txn_amount_confirm', 'txn_datetime_confirm'],
    )
    readiness = build_readiness(
        intent=Intent.SuspiciousTransaction,
        missing_fields=['txn_amount_confirm', 'txn_datetime_confirm'],
        tools=tools,
        case_status='open',
    )

    timeline = [
        _timeline_row(1, 'case_created', {'intent': Intent.SuspiciousTransaction.value}),
        _timeline_row(2, 'profile_confirmed', {'stored': 1}),
        _timeline_row(3, 'tool_result', {'tool': 'get_transactions', 'result': {'transactions': []}}),
    ]

    dossier = build_case_dossier(case_obj, readiness=readiness, timeline_rows=timeline)

    assert dossier.case_id == 'case-1'
    assert dossier.intent == Intent.SuspiciousTransaction
    assert len(dossier.actions_taken) == 3
    assert dossier.current_status == 'open'
    assert 'Статус кейса' in dossier.operator_safe_context
    assert dossier.next_expected_step


def test_case_dossier_for_closed_case_keeps_completed_context():
    case_obj = SimpleNamespace(
        id='case-2',
        case_type=Intent.StatusWhatNext.value,
        summary_public='Клиент уточняет статус обращения.',
        dispute_reason='',
        facts_confirmed_json='["case_id"]',
        facts_pending_json='[]',
        priority='medium',
        status='closed',
    )

    tools = resolve_tools(
        Intent.StatusWhatNext,
        Phase.Explain,
        missing_fields=[],
    )
    readiness = build_readiness(
        intent=Intent.StatusWhatNext,
        missing_fields=[],
        tools=tools,
        case_status='closed',
    )

    dossier = build_case_dossier(case_obj, readiness=readiness, timeline_rows=[])

    assert dossier.current_status == 'closed'
    assert dossier.risk_summary.risk_level.value in {'low', 'high'}
    assert dossier.operator_safe_context