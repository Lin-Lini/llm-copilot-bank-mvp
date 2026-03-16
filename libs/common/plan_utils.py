"""
Utility functions to deterministically update the copilot plan and phase.
"""

from __future__ import annotations

from contracts.schemas import AnalyzeV1, Phase, Plan, PlanStep, ToolName


def reduce_plan_after_analyze(plan: Plan, an: AnalyzeV1) -> Plan:
    steps: list[PlanStep] = []
    done_ids: set[str] = set()

    if an.phase == Phase.Act:
        done_ids.update({'collect_core', 'risk_check', 'collect'})
    elif an.phase == Phase.Explain:
        done_ids.update({'collect_core', 'risk_check', 'act_get_txn', 'case_create', 'collect', 'act'})

    for s in plan.steps:
        steps.append(s.model_copy(update={'done': s.id in done_ids or s.done}))

    current_step_id = plan.current_step_id
    if an.phase == Phase.Act:
        current_step_id = 'act_get_txn' if any(s.id == 'act_get_txn' for s in plan.steps) else 'act'
    elif an.phase == Phase.Explain:
        current_step_id = 'explain_next' if any(s.id == 'explain_next' for s in plan.steps) else 'explain'
    return plan.model_copy(update={'steps': steps, 'current_step_id': current_step_id})


def reduce_plan_after_tool(plan: Plan, tool_name: str) -> Plan:
    steps: list[PlanStep] = []
    mark_done: dict[str, bool] = {}
    next_step = plan.current_step_id

    if tool_name == ToolName.get_transactions.value:
        mark_done['act_get_txn'] = True
        mark_done['act'] = True
        next_step = 'case_create' if any(s.id == 'case_create' for s in plan.steps) else 'explain'
    elif tool_name == ToolName.create_case.value:
        mark_done['case_create'] = True
        next_step = 'explain_next' if any(s.id == 'explain_next' for s in plan.steps) else 'explain'
    elif tool_name in {
        ToolName.block_card.value,
        ToolName.unblock_card.value,
        ToolName.reissue_card.value,
        ToolName.set_card_limits.value,
        ToolName.toggle_online_payments.value,
    }:
        mark_done['act'] = True
        next_step = 'explain'

    for s in plan.steps:
        if s.id in mark_done:
            steps.append(s.model_copy(update={'done': True}))
        else:
            steps.append(s)

    return plan.model_copy(update={'steps': steps, 'current_step_id': next_step})


def phase_from_plan(plan: Plan) -> Phase:
    current = plan.current_step_id
    if current in {'explain', 'explain_next'}:
        return Phase.Explain
    if current in {'act', 'act_get_txn', 'case_create'}:
        return Phase.Act
    return Phase.Collect
