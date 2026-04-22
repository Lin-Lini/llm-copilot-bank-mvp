from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import InternalCreateCaseRequest, Intent
from apps.backend.app.core.deps import get_db
from libs.common.case_dossier_store import get_case_dossier_payload
from libs.common.case_readiness import build_readiness, infer_case_phase, normalize_intent, required_pending_fields
from libs.common.json_lists import normalize_string_list, parse_string_list
from libs.common.models import Case, CaseTimeline
from libs.common.security import require_service
from libs.common.state_engine import resolve_tools

router = APIRouter(prefix='/_internal', tags=['internal'])


def _priority(intent: Intent) -> str:
    if intent in {Intent.SuspiciousTransaction, Intent.LostStolen}:
        return 'high'
    return 'medium'


@router.post('/cases/create')
async def internal_create_case(
    req: InternalCreateCaseRequest,
    actor=Depends(require_service),
    db: AsyncSession = Depends(get_db),
):
    sla_deadline = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    pending_fields = required_pending_fields(req.intent)

    c = Case(
        conversation_id=req.conversation_id,
        case_type=req.intent.value,
        priority=_priority(req.intent),
        sla_deadline=sla_deadline,
        dispute_reason=req.intent.value if req.intent == Intent.SuspiciousTransaction else '',
        facts_pending_json=normalize_string_list(pending_fields),
        status='open',
        summary_public=req.summary_public,
    )
    db.add(c)
    await db.commit()

    created_payload = {
        'intent': req.intent.value,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'facts_pending': pending_fields,
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

    intent = normalize_intent(c.case_type)
    facts_pending = parse_string_list(c.facts_pending_json)
    phase = infer_case_phase(intent, facts_pending, c.status)
    tools_ui = resolve_tools(intent, phase, missing_fields=facts_pending)

    readiness = build_readiness(
        intent=intent,
        missing_fields=facts_pending,
        tools=tools_ui,
        case_status=c.status,
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