from contracts.schemas import Phase, Plan, PlanStep

from libs.common.plan_utils import phase_from_plan, reduce_plan_after_tool


def test_reduce_plan_after_create_case_stays_deterministic():
    plan = Plan(
        current_step_id='case_create',
        steps=[
            PlanStep(id='collect_core', title='collect', done=True),
            PlanStep(id='risk_check', title='risk', done=True),
            PlanStep(id='act_get_txn', title='txn', done=True),
            PlanStep(id='case_create', title='case', done=False),
            PlanStep(id='explain_next', title='explain', done=False),
        ],
    )

    new_plan = reduce_plan_after_tool(plan, 'create_case')

    assert new_plan.current_step_id == 'explain_next'
    assert phase_from_plan(new_plan) == Phase.Explain
    assert any(step.id == 'case_create' and step.done for step in new_plan.steps)
