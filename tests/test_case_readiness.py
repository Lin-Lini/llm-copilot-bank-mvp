from contracts.schemas import Intent, Phase, ToolName
from libs.common.case_readiness import build_missing_field_meta, build_readiness, infer_case_phase, required_pending_fields
from libs.common.state_engine import resolve_tools


def test_required_pending_fields_for_suspicious_transaction():
    assert required_pending_fields(Intent.SuspiciousTransaction) == [
        'card_in_possession',
        'txn_amount_confirm',
        'txn_datetime_confirm',
        'customer_confirm_block',
    ]


def test_build_missing_field_meta_contains_human_labels_and_questions():
    items = build_missing_field_meta(Intent.SuspiciousTransaction, ['txn_datetime_confirm'])

    assert len(items) == 1
    assert items[0].label == 'Подтвержденные дата и время операции'
    assert 'дату и время' in (items[0].suggested_question or '').lower()


def test_readiness_is_needs_info_when_high_severity_fields_missing():
    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Collect,
        missing_fields=['card_in_possession', 'txn_amount_confirm'],
    )
    readiness = build_readiness(
        intent=Intent.SuspiciousTransaction,
        missing_fields=['card_in_possession', 'txn_amount_confirm'],
        tools=tools,
        case_status='open',
    )

    assert readiness.status.value == 'needs_info'
    assert 'card_in_possession' in readiness.blockers
    assert readiness.score < 100


def test_readiness_is_ready_when_no_missing_and_actionable_tool_exists():
    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Act,
        missing_fields=[],
    )
    readiness = build_readiness(
        intent=Intent.SuspiciousTransaction,
        missing_fields=[],
        tools=tools,
        case_status='open',
    )

    assert readiness.status.value == 'ready'
    assert any(item.tool == ToolName.get_transactions for item in readiness.ready_tools)


def test_infer_case_phase_prefers_explain_for_closed_case():
    assert infer_case_phase(Intent.SuspiciousTransaction, [], 'closed') == Phase.Explain

def test_readiness_completed_clears_missing_fields_and_blockers():
    tools = resolve_tools(
        Intent.StatusWhatNext,
        Phase.Explain,
        missing_fields=['case_id'],
    )
    readiness = build_readiness(
        intent=Intent.StatusWhatNext,
        missing_fields=['case_id'],
        tools=tools,
        case_status='closed',
    )

    assert readiness.status.value == 'completed'
    assert readiness.score == 100
    assert readiness.blockers == []
    assert readiness.missing_fields == []