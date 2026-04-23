from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    DisputeSubtype,
    InternalCreateCaseRequest,
    Intent,
    StatusContext,
)
from apps.backend.app.core.deps import get_db
from libs.common.case_dossier import build_analyze_from_case_context
from libs.common.case_dossier_store import get_case_dossier_payload
from libs.common.case_readiness import build_readiness, infer_case_phase, normalize_intent, required_pending_fields
from libs.common.json_lists import normalize_string_list, parse_string_list
from libs.common.models import Case, CaseTimeline
from libs.common.redis_client import get_redis
from libs.common.security import require_service
from libs.common.state_engine import resolve_tools

router = APIRouter(prefix='/_internal', tags=['internal'])


def _state_key(conversation_id: str) -> str:
    return f'copilot:state:{conversation_id}'


async def _load_analyze_from_state(conversation_id: str) -> AnalyzeV1 | None:
    raw = await get_redis().get(_state_key(conversation_id))
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except Exception:
        return None
    last_analyze = state.get('last_analyze')
    if not isinstance(last_analyze, dict):
        return None
    try:
        return AnalyzeV1.model_validate(last_analyze)
    except Exception:
        return None


def _priority(intent: Intent, analyze: AnalyzeV1 | None) -> str:
    if intent in {Intent.BlockCard, Intent.LostStolen}:
        return 'high'
    if intent == Intent.SuspiciousTransaction:
        if analyze and (
            analyze.facts.compromise_signals
            or analyze.facts.dispute_subtype == DisputeSubtype.suspicious
        ):
            return 'high'
        if analyze and analyze.facts.dispute_subtype in {
            DisputeSubtype.recurring_subscription,
            DisputeSubtype.duplicate_charge,
            DisputeSubtype.reversal_pending,
        }:
            return 'medium'
        return 'high'
    if intent == Intent.StatusWhatNext:
        return 'low'
    return 'medium'


def _derive_confirmed_fields(analyze: AnalyzeV1 | None) -> list[str]:
    if analyze is None:
        return []

    confirmed: list[str] = []
    if analyze.facts.card_state == CardState.with_client:
        confirmed.append('card_in_possession')
    if analyze.facts.card_state != CardState.unknown:
        confirmed.append('card_state')
    if analyze.facts.dispute_subtype != DisputeSubtype.unknown:
        confirmed.append('dispute_subtype')
    if analyze.facts.status_context != StatusContext.unknown:
        confirmed.append('status_context')
    if analyze.facts.compromise_signals:
        confirmed.append('compromise_signals')
    if analyze.facts.requested_actions:
        confirmed.append('requested_actions')
    if analyze.facts.dispute_subtype == DisputeSubtype.recurring_subscription and analyze.facts.merchant_hint:
        confirmed.append('merchant_name_confirm')
    return normalize_string_list(confirmed)


def _derive_dispute_reason(intent: Intent, analyze: AnalyzeV1 | None) -> str:
    if analyze is None:
        return intent.value if intent == Intent.SuspiciousTransaction else ''

    if intent == Intent.SuspiciousTransaction:
        subtype = analyze.facts.dispute_subtype
        return subtype.value if subtype != DisputeSubtype.unknown else intent.value

    if intent in {Intent.LostStolen, Intent.UnblockReissue, Intent.CardNotWorking}:
        if analyze.facts.card_state != CardState.unknown:
            return analyze.facts.card_state.value

    if intent == Intent.StatusWhatNext:
        return analyze.facts.status_context.value if analyze.facts.status_context != StatusContext.unknown else intent.value

    return intent.value


def _decision_summary(intent: Intent, analyze: AnalyzeV1 | None, readiness) -> str:
    if intent == Intent.SuspiciousTransaction:
        if analyze and analyze.facts.dispute_subtype == DisputeSubtype.recurring_subscription:
            return 'Кейс по регулярному списанию или подписке; следующий шаг — сверка операций и подтверждение сервиса.'
        if analyze and analyze.facts.dispute_subtype == DisputeSubtype.duplicate_charge:
            return 'Кейс по возможному двойному списанию; следующий шаг — сверка повторной операции.'
        if analyze and analyze.facts.dispute_subtype == DisputeSubtype.reversal_pending:
            return 'Кейс по холду или резерву; следующий шаг — уточнение статуса операции.'
        return 'Кейс по спорной операции; следующий шаг — подтверждение параметров операции и безопасных действий.'

    if intent in {Intent.BlockCard, Intent.LostStolen}:
        return 'Кейс повышенного риска; приоритетный шаг — блокировка карты и фиксация дальнейших действий.'

    if intent == Intent.UnblockReissue:
        return 'Нужно различить разблокировку и перевыпуск карты и действовать только по подтвержденному сценарию.'

    if intent == Intent.CardNotWorking:
        return 'Следующий шаг — уточнить канал проблемы и исключить лимиты, настройки или повреждение карты.'

    if intent == Intent.StatusWhatNext:
        return 'Следующий шаг — сообщить подтвержденный статус кейса и ожидаемое дальнейшее действие.'

    return readiness.next_action


def build_case_seed(req: InternalCreateCaseRequest, analyze: AnalyzeV1 | None) -> dict[str, Any]:
    intent = req.intent if req.intent != Intent.Unknown else (analyze.intent if analyze else Intent.Unknown)
    summary_public = str(req.summary_public or '').strip() or (analyze.summary_public if analyze else '') or f'Обращение по сценарию {intent.value}.'

    facts_confirmed = _derive_confirmed_fields(analyze)
    facts_pending = required_pending_fields(intent, analyze)
    phase = infer_case_phase(intent, facts_pending, 'open', analyze)
    tools_ui = resolve_tools(
        intent,
        phase,
        missing_fields=facts_pending,
        confirmed_fields=facts_confirmed,
        analyze=analyze,
    )
    readiness = build_readiness(
        intent=intent,
        missing_fields=facts_pending,
        tools=tools_ui,
        case_status='open',
        analyze=analyze,
    )

    dispute_reason = _derive_dispute_reason(intent, analyze)
    decision_summary = _decision_summary(intent, analyze, readiness)

    created_payload = {
        'intent': intent.value,
        'phase': phase.value,
        'priority': _priority(intent, analyze),
        'facts_confirmed': facts_confirmed,
        'facts_pending': facts_pending,
        'dispute_reason': dispute_reason,
        'summary_public': summary_public,
        'decision_summary': decision_summary,
        'readiness': readiness.model_dump(),
        'domain_context': {
            'dispute_subtype': analyze.facts.dispute_subtype.value if analyze else DisputeSubtype.unknown.value,
            'card_state': analyze.facts.card_state.value if analyze else CardState.unknown.value,
            'requested_actions': [item.value for item in (analyze.facts.requested_actions if analyze else [])],
            'status_context': analyze.facts.status_context.value if analyze else StatusContext.unknown.value,
            'compromise_signals': [item.value for item in (analyze.facts.compromise_signals if analyze else [])],
        },
        'analyze_snapshot': analyze.model_dump() if analyze else None,
    }

    return {
        'intent': intent,
        'summary_public': summary_public,
        'priority': _priority(intent, analyze),
        'dispute_reason': dispute_reason,
        'facts_confirmed': facts_confirmed,
        'facts_pending': facts_pending,
        'phase': phase,
        'readiness': readiness,
        'decision_summary': decision_summary,
        'created_payload': created_payload,
    }


@router.post('/cases/create')
async def internal_create_case(
    req: InternalCreateCaseRequest,
    actor=Depends(require_service),
    db: AsyncSession = Depends(get_db),
):
    analyze = await _load_analyze_from_state(req.conversation_id)
    seed = build_case_seed(req, analyze)
    sla_deadline = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

    c = Case(
        conversation_id=req.conversation_id,
        case_type=seed['intent'].value,
        priority=seed['priority'],
        sla_deadline=sla_deadline,
        dispute_reason=seed['dispute_reason'],
        facts_confirmed_json=seed['facts_confirmed'],
        facts_pending_json=seed['facts_pending'],
        decision_summary=seed['decision_summary'],
        status='open',
        summary_public=seed['summary_public'],
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)

    created_payload = {
        **seed['created_payload'],
        'case_id': c.id,
        'sla_deadline': c.sla_deadline,
    }
    tl = CaseTimeline(
        case_id=c.id,
        kind='case_created',
        payload=json.dumps(created_payload, ensure_ascii=False),
        payload_json=created_payload,
    )
    db.add(tl)
    await db.commit()

    return {
        'case_id': c.id,
        'status': c.status,
        'case_type': c.case_type,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'created_at': c.created_at.isoformat(),
    }


@router.get('/cases/status')
async def internal_case_status(case_id: str, actor=Depends(require_service), db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not c:
        return {'error': 'not_found'}

    tl = (
        await db.execute(
            select(CaseTimeline).where(CaseTimeline.case_id == case_id).order_by(CaseTimeline.id.asc())
        )
    ).scalars().all()

    analyze = build_analyze_from_case_context(c, tl)
    intent = normalize_intent(c.case_type)
    facts_pending = parse_string_list(c.facts_pending_json)
    facts_confirmed = parse_string_list(c.facts_confirmed_json)
    phase = infer_case_phase(intent, facts_pending, c.status, analyze)
    tools_ui = resolve_tools(
        intent,
        phase,
        missing_fields=facts_pending,
        confirmed_fields=facts_confirmed,
        analyze=analyze,
    )
    readiness = build_readiness(
        intent=intent,
        missing_fields=facts_pending,
        tools=tools_ui,
        case_status=c.status,
        analyze=analyze,
    )

    dossier = await get_case_dossier_payload(db, c, timeline_rows=tl)

    return {
        'case_id': c.id,
        'status': c.status,
        'case_type': c.case_type,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'readiness': readiness.model_dump(),
        'dossier': dossier,
        'timeline': [
            {
                'id': t.id,
                'kind': t.kind,
                'payload': t.payload_json if t.payload_json is not None else (json.loads(t.payload) if t.payload else {}),
                'created_at': t.created_at.isoformat(),
            }
            for t in tl
        ],
    }