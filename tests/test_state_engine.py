from contracts.schemas import AnalyzeFacts, AnalyzeV1, Intent, Phase, ProfileUpdate, RiskLevel, Severity, ToolName
from contracts.schemas import DangerFlag, RiskChecklistItem
from libs.common.state_engine import build_plan, phase_from_plan, reduce_plan_after_analyze, resolve_tools


def _analyze(phase: Phase) -> AnalyzeV1:
    return AnalyzeV1(
        schema_version='1.0',
        intent=Intent.SuspiciousTransaction,
        phase=phase,
        confidence=0.9,
        summary_public='test',
        risk_level=RiskLevel.low,
        facts=AnalyzeFacts(
            card_hint=None,
            txn_hint=None,
            amount=None,
            currency=None,
            datetime_hint=None,
            merchant_hint=None,
            channel_hint='unknown',
            customer_claim='unknown',
            card_in_possession='unknown',
            delivery_pref=None,
            previous_actions=[],
        ),
        profile_update=ProfileUpdate(
            client_card_context='',
            recurring_issues=[],
            notes_for_case_file='',
        ),
        missing_fields=[],
        next_questions=[],
        tools_suggested=[],
        danger_flags=[DangerFlag(type='none', severity=Severity.low, text='')],
        risk_checklist=[RiskChecklistItem(id='no_cvv', severity=Severity.high, text='Не запрашивать CVV/CVC.')],
        analytics_tags=[],
    )


def test_reduce_plan_after_analyze_moves_to_act_step():
    plan = build_plan(Intent.SuspiciousTransaction)
    new_plan = reduce_plan_after_analyze(plan, _analyze(Phase.Act))

    assert new_plan.current_step_id == 'act_get_txn'
    assert phase_from_plan(new_plan) == Phase.Act
    assert {step.id for step in new_plan.steps if step.done} == {'collect_core', 'risk_check'}


def test_resolve_tools_disables_get_transactions_until_required_fields_collected():
    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Act,
        missing_fields=['card_in_possession', 'txn_amount_confirm'],
    )

    txn_tool = next(tool for tool in tools if tool.tool == ToolName.get_transactions)
    assert txn_tool.enabled is False
    assert 'сумму и время операции' in txn_tool.reason


def test_resolve_tools_enables_block_card_for_high_risk_intent_even_without_confirmation():
    tools = resolve_tools(
        Intent.LostStolen,
        Phase.Collect,
        missing_fields=['customer_confirm_block'],
    )

    block_tool = next(tool for tool in tools if tool.tool == ToolName.block_card)
    assert block_tool.enabled is True
    assert 'повышенного риска' in block_tool.reason


def test_resolve_tools_restricts_safe_mode_to_safe_actions():
    tools = resolve_tools(
        Intent.SuspiciousTransaction,
        Phase.Act,
        missing_fields=[],
        safe_mode='warn',
    )

    assert any(tool.tool == ToolName.create_case and tool.enabled for tool in tools)
    assert all(tool.enabled is False for tool in tools if tool.tool != ToolName.create_case)