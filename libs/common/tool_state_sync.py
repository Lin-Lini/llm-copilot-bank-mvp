from __future__ import annotations

from typing import Any

from contracts.schemas import Plan
from libs.common.case_readiness import infer_case_phase, normalize_intent, required_pending_fields
from libs.common.state_engine import build_plan


def _mark_case_created(plan_dict: dict[str, Any]) -> dict[str, Any]:
    plan = Plan.model_validate(plan_dict)
    new_steps = []

    for step in plan.steps:
        if step.id == 'case_create':
            new_steps.append(step.model_copy(update={'done': True}))
        else:
            new_steps.append(step)

    current_step_id = plan.current_step_id
    if any(step.id == 'collect_core' for step in new_steps):
        current_step_id = 'collect_core'

    return Plan(current_step_id=current_step_id, steps=new_steps).model_dump()


def sync_after_create_case(prev_state: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
    case_intent = normalize_intent(tool_result.get('case_type') or prev_state.get('intent'))
    case_status = str(tool_result.get('status') or 'open')
    pending_fields = required_pending_fields(case_intent)
    phase = infer_case_phase(case_intent, pending_fields, case_status)

    base_plan = build_plan(case_intent).model_dump()
    synced_plan = _mark_case_created(base_plan)

    last_analyze = prev_state.get('last_analyze')
    if not isinstance(last_analyze, dict):
        last_analyze = {}

    synced_analyze = {
        **last_analyze,
        'intent': case_intent.value,
        'phase': phase.value,
        'missing_fields': pending_fields,
        'analytics_tags': ['suspicious_transaction'] if case_intent.value == 'SuspiciousTransaction' else last_analyze.get('analytics_tags', []),
    }

    if case_intent.value == 'SuspiciousTransaction':
        synced_analyze['tools_suggested'] = [
            {
                'tool': 'get_transactions',
                'reason': 'Нужно сверить детали спорной операции по списку транзакций.',
                'params_hint': {'date_range': 'последние 7 дней'},
            },
            {
                'tool': 'create_case',
                'reason': 'Для корректной обработки спорной операции нужно зарегистрировать обращение.',
                'params_hint': {'intent': 'SuspiciousTransaction'},
            },
        ]

    if not synced_analyze.get('summary_public'):
        synced_analyze['summary_public'] = f'Создан кейс по сценарию {case_intent.value}.'

    return {
        'conversation_id': prev_state.get('conversation_id'),
        'intent': case_intent.value,
        'phase': phase.value,
        'plan': synced_plan,
        'last_analyze': synced_analyze,
        'last_draft': None,
    }