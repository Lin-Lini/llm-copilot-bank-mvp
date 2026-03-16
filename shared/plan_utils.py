"""
Utility functions to deterministically update the copilot plan and phase.

The copilot orchestrator uses a plan to guide the operator through
Collect → Act → Explain steps.  While the LLM can suggest updates in
explain mode, the backend must enforce a deterministic progression
independent of the model to meet regulatory requirements and avoid
inconsistent states.  These reducers apply transitions based on
analysis results and tool executions.
"""

from __future__ import annotations

from typing import Optional

from contracts.schemas import AnalyzeV1, Plan, PlanStep, Phase, ToolName


def reduce_plan_after_analyze(plan: Plan, an: AnalyzeV1) -> Plan:
    """Update the plan based on the phase from ANALYZE.

    - In Phase.Collect the plan remains in 'collect_core'.
    - In Phase.Act the collect steps are marked done and the current step
      moves to the first action step.
    - In Phase.Explain the action and case creation steps are marked done,
      and the current step moves to the explanation.
    """
    # Copy steps so we don't mutate the original plan
    steps: list[PlanStep] = []
    step_map = {s.id: s for s in plan.steps}
    # Determine which steps should be marked done based on the new phase
    done_ids: set[str] = set()
    if an.phase == Phase.Act:
        # Collect steps complete: collect_core, risk_check
        done_ids.update({'collect_core', 'risk_check'})
    elif an.phase == Phase.Explain:
        # All prior steps complete
        done_ids.update({'collect_core', 'risk_check', 'act_get_txn', 'case_create'})
    # Build updated steps list
    for s in plan.steps:
        steps.append(s.model_copy(update={'done': s.id in done_ids}))
    # Determine new current_step_id
    current_step_id = plan.current_step_id
    if an.phase == Phase.Act:
        current_step_id = 'act_get_txn'
    elif an.phase == Phase.Explain:
        current_step_id = 'explain_next'
    return plan.model_copy(update={'steps': steps, 'current_step_id': current_step_id})


def reduce_plan_after_tool(plan: Plan, tool_name: str) -> Plan:
    """Update the plan after a tool execution.

    - After get_transactions: mark act_get_txn done, move to case_create.
    - After create_case: mark case_create done, move to explain_next.
    - For other tools, no change.
    """
    steps: list[PlanStep] = []
    # Mark specific steps done based on tool
    mark_done: dict[str, bool] = {}
    if tool_name == ToolName.get_transactions.value:
        mark_done['act_get_txn'] = True
        next_step = 'case_create'
    elif tool_name == ToolName.create_case.value:
        mark_done['case_create'] = True
        next_step = 'explain_next'
    else:
        # default: remain on current step
        next_step = plan.current_step_id
    # Build updated steps
    for s in plan.steps:
        if s.id in mark_done:
            steps.append(s.model_copy(update={'done': True}))
        else:
            steps.append(s)
    return plan.model_copy(update={'steps': steps, 'current_step_id': next_step})