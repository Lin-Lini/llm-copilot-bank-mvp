from contracts.schemas import (
    AnalyzeFacts,
    AnalyzeV1,
    CardState,
    ChannelHint,
    CompromiseSignal,
    DisputeSubtype,
    Intent,
    Phase,
    ProfileUpdate,
    RequestedAction,
    RiskLevel,
    Severity,
    StatusContext,
    ToolName,
)
from contracts.schemas import DangerFlag, RiskChecklistItem
from libs.common.case_readiness import build_readiness, required_pending_fields
from libs.common.copilot_postprocess import repair_draft
from libs.common.llm_stub import draft as stub_draft
from libs.common.state_engine import build_plan, resolve_tools
from libs.common.tool_state_sync import sync_after_create_case


def _analyze(
    *,
    intent: Intent,
    phase: Phase,
    dispute_subtype: DisputeSubtype = DisputeSubtype.unknown,
    card_state: CardState = CardState.unknown,
    requested_actions: list[RequestedAction] | None = None,
    status_context: StatusContext = StatusContext.unknown,
    compromise_signals: list[CompromiseSignal] | None = None,
    missing_fields: list[str] | None = None,
    channel_hint: ChannelHint = ChannelHint.unknown,
) -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=intent,
        phase=phase,
        confidence=0.9,
        summary_public='test',
        risk_level=RiskLevel.medium,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint=channel_hint,
            customer_claim='unknown',
            card_in_possession='unknown',
            delivery_pref=None,
            previous_actions=[],
            dispute_subtype=dispute_subtype,
            card_state=card_state,
            requested_actions=requested_actions or [],
            status_context=status_context,
            compromise_signals=compromise_signals or [],
        ),
        profile_update=ProfileUpdate(client_card_context='', recurring_issues=[], notes_for_case_file=''),
        missing_fields=missing_fields or [],
        next_questions=[],
        tools_suggested=[],
        danger_flags=[DangerFlag(type='none', severity=Severity.low, text='')],
        risk_checklist=[RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.')],
        analytics_tags=[],
    )


def test_recurring_subscription_blocks_card_and_requires_merchant_name():
    an = _analyze(
        intent=Intent.SuspiciousTransaction,
        phase=Phase.Collect,
        dispute_subtype=DisputeSubtype.recurring_subscription,
        requested_actions=[RequestedAction.investigate_transaction],
        missing_fields=['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'merchant_name_confirm'],
    )

    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Collect,
        missing_fields=an.missing_fields,
        analyze=an,
    )

    txn_tool = next(tool for tool in tools if tool.tool == ToolName.get_transactions)
    block_tool = next(tool for tool in tools if tool.tool == ToolName.block_card)

    assert txn_tool.enabled is False
    assert 'подписки' in txn_tool.reason.lower() or 'сервиса' in txn_tool.reason.lower()
    assert block_tool.enabled is False

    pending = required_pending_fields(Intent.SuspiciousTransaction, an)
    assert 'merchant_name_confirm' in pending


def test_lost_stolen_enables_block_and_reissue_when_card_lost():
    an = _analyze(
        intent=Intent.LostStolen,
        phase=Phase.Act,
        card_state=CardState.lost,
        requested_actions=[RequestedAction.block_card, RequestedAction.reissue_card],
    )

    tools = resolve_tools(Intent.LostStolen, Phase.Act, missing_fields=[], analyze=an)

    block_tool = next(tool for tool in tools if tool.tool == ToolName.block_card)
    reissue_tool = next(tool for tool in tools if tool.tool == ToolName.reissue_card)

    assert block_tool.enabled is True
    assert reissue_tool.enabled is True


def test_card_not_working_prefers_limits_tools_and_not_block():
    an = _analyze(
        intent=Intent.CardNotWorking,
        phase=Phase.Act,
        channel_hint=ChannelHint.online,
        card_state=CardState.with_client,
    )

    tools = resolve_tools(Intent.CardNotWorking, Phase.Act, missing_fields=[], analyze=an)
    tool_names = {tool.tool for tool in tools}

    assert ToolName.get_card_limits in tool_names
    assert ToolName.toggle_online_payments in tool_names
    assert ToolName.block_card not in tool_names

    toggle_tool = next(tool for tool in tools if tool.tool == ToolName.toggle_online_payments)
    assert toggle_tool.enabled is True


def test_status_what_next_becomes_ready_with_known_case_context():
    an = _analyze(
        intent=Intent.StatusWhatNext,
        phase=Phase.Explain,
        status_context=StatusContext.case_known,
        requested_actions=[RequestedAction.get_case_status],
    )

    pending = required_pending_fields(Intent.StatusWhatNext, an)
    tools = resolve_tools(Intent.StatusWhatNext, Phase.Explain, missing_fields=pending, analyze=an)
    readiness = build_readiness(intent=Intent.StatusWhatNext, missing_fields=pending, tools=tools, case_status='open', analyze=an)

    assert pending == []
    assert readiness.status.value == 'ready'
    assert any(item.tool == ToolName.get_case_status for item in readiness.ready_tools)


def test_repair_draft_recomputes_sidebar_tools_for_recurring_case():
    an = _analyze(
        intent=Intent.SuspiciousTransaction,
        phase=Phase.Collect,
        dispute_subtype=DisputeSubtype.recurring_subscription,
        requested_actions=[RequestedAction.investigate_transaction],
        missing_fields=['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm', 'merchant_name_confirm'],
    )

    raw = stub_draft(an, build_plan(Intent.SuspiciousTransaction), [], [])
    fixed = repair_draft(raw, an)

    assert any(tool.tool == ToolName.get_transactions and tool.enabled is False for tool in fixed.sidebar.tools)
    assert 'подпис' in fixed.sidebar.operator_notes.lower() or 'сервис' in fixed.sidebar.operator_notes.lower()
    assert fixed.sidebar.readiness.next_action


def test_sync_after_create_case_preserves_dispute_subtype():
    prev_state = {
        'conversation_id': 'conv-1',
        'intent': 'SuspiciousTransaction',
        'phase': 'Collect',
        'plan': {'current_step_id': 'collect_core', 'steps': []},
        'last_analyze': _analyze(
            intent=Intent.SuspiciousTransaction,
            phase=Phase.Collect,
            dispute_subtype=DisputeSubtype.duplicate_charge,
            requested_actions=[RequestedAction.investigate_transaction],
            missing_fields=['txn_datetime_confirm'],
        ).model_dump(),
        'last_draft': {'sidebar': {'intent': 'SuspiciousTransaction'}},
    }

    synced = sync_after_create_case(
        prev_state,
        {'case_id': 'case-1', 'case_type': 'SuspiciousTransaction', 'status': 'open'},
    )

    assert synced['last_analyze']['facts']['dispute_subtype'] == 'duplicate_charge'
    assert synced['last_analyze']['tools_suggested'][0]['tool'] == 'get_transactions'