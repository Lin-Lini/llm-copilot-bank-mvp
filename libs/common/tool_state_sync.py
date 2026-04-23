from __future__ import annotations

from typing import Any

from contracts.schemas import AnalyzeV1, DisputeSubtype, Intent, Plan, RequestedAction, StatusContext
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


def _tools_suggested(intent: Intent, subtype: DisputeSubtype) -> list[dict[str, Any]]:
    if intent == Intent.StatusWhatNext:
        return [
            {'tool': 'get_case_status', 'reason': 'Нужно сообщить подтвержденный статус обращения.', 'params_hint': {}},
        ]
    if intent == Intent.LostStolen:
        return [
            {'tool': 'block_card', 'reason': 'Нужно выполнить блокировку карты по подтвержденному сценарию риска.', 'params_hint': {}},
            {'tool': 'create_case', 'reason': 'Нужно зафиксировать кейс утраты или кражи карты.', 'params_hint': {'intent': intent.value}},
            {'tool': 'reissue_card', 'reason': 'После блокировки может потребоваться перевыпуск карты.', 'params_hint': {}},
        ]
    if intent == Intent.UnblockReissue:
        return [
            {'tool': 'unblock_card', 'reason': 'Если разблокировка допустима, можно подтвердить следующий шаг.', 'params_hint': {}},
            {'tool': 'reissue_card', 'reason': 'Если разблокировка недопустима или карта повреждена, нужен перевыпуск.', 'params_hint': {}},
        ]
    if intent == Intent.CardNotWorking:
        return [
            {'tool': 'get_card_limits', 'reason': 'Нужно проверить лимиты и ограничения карты.', 'params_hint': {}},
            {'tool': 'toggle_online_payments', 'reason': 'Если проблема связана с онлайн-оплатой, можно проверить настройки.', 'params_hint': {}},
        ]
    if intent == Intent.SuspiciousTransaction:
        reason = 'Нужно сверить детали спорной операции по списку транзакций.'
        if subtype == DisputeSubtype.recurring_subscription:
            reason = 'Нужно проверить регулярное списание или подписку и подтвердить детали операции.'
        elif subtype == DisputeSubtype.duplicate_charge:
            reason = 'Нужно сравнить дублирующиеся операции и исключить повторное списание.'
        elif subtype == DisputeSubtype.reversal_pending:
            reason = 'Нужно проверить статус холда или незавершенного списания.'
        return [
            {'tool': 'get_transactions', 'reason': reason, 'params_hint': {'date_range': 'последние 7 дней'}},
            {'tool': 'create_case', 'reason': 'Для корректной обработки спорной операции нужно зарегистрировать обращение.', 'params_hint': {'intent': intent.value}},
        ]
    return []


def sync_after_create_case(prev_state: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
    case_intent = normalize_intent(tool_result.get('case_type') or prev_state.get('intent'))
    case_status = str(tool_result.get('status') or 'open')

    last_analyze = prev_state.get('last_analyze')
    if not isinstance(last_analyze, dict):
        last_analyze = {}

    try:
        analyze_obj = AnalyzeV1.model_validate({
            **last_analyze,
            'intent': case_intent.value,
        })
    except Exception:
        analyze_obj = None

    pending_fields = required_pending_fields(case_intent, analyze_obj)
    phase = infer_case_phase(case_intent, pending_fields, case_status, analyze_obj)

    base_plan = build_plan(case_intent).model_dump()
    synced_plan = _mark_case_created(base_plan)

    subtype = analyze_obj.facts.dispute_subtype if analyze_obj else DisputeSubtype.unknown

    tags = list(last_analyze.get('analytics_tags', []))
    if case_intent == Intent.SuspiciousTransaction:
        tags = ['suspicious_transaction']
        if subtype != DisputeSubtype.unknown:
            tags.append(subtype.value)

    synced_analyze = {
        **last_analyze,
        'intent': case_intent.value,
        'phase': phase.value,
        'missing_fields': pending_fields,
        'analytics_tags': tags,
        'tools_suggested': _tools_suggested(case_intent, subtype),
    }

    if case_intent == Intent.StatusWhatNext and analyze_obj is not None:
        synced_analyze.setdefault('facts', {})
        synced_analyze['facts'] = {
            **last_analyze.get('facts', {}),
            'status_context': StatusContext.case_known.value,
            'requested_actions': [RequestedAction.get_case_status.value],
        }

    if case_intent == Intent.SuspiciousTransaction and not synced_analyze.get('analytics_tags'):
        tags = ['suspicious_transaction']
        if subtype != DisputeSubtype.unknown:
            tags.append(subtype.value)
        synced_analyze['analytics_tags'] = tags

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