from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.access import require_case_access, require_conversation_access
from apps.backend.app.core.deps import get_db
from libs.common.models import Case, CaseTimeline
from libs.common.security import require_actor, require_operator


router = APIRouter(prefix='/cases', tags=['cases'])


def _case_payload(c: Case) -> dict:
    return {
        'case_id': c.id,
        'conversation_id': c.conversation_id,
        'case_type': c.case_type,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'customer_ref_masked': c.customer_ref_masked,
        'card_ref_masked': c.card_ref_masked,
        'operation_ref': c.operation_ref,
        'dispute_reason': c.dispute_reason,
        'facts_confirmed': json.loads(c.facts_confirmed_json or '[]'),
        'facts_pending': json.loads(c.facts_pending_json or '[]'),
        'decision_summary': c.decision_summary,
        'status': c.status,
        'summary_public': c.summary_public,
        'notes': c.notes,
        'created_at': c.created_at.isoformat(),
        'updated_at': c.updated_at.isoformat(),
    }


@router.get('')
async def list_cases(
    conversation_id: str | None = None,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    q = select(Case)

    if conversation_id:
        await require_conversation_access(db, actor, conversation_id)
        q = q.where(Case.conversation_id == conversation_id)
    elif actor['role'] != 'operator':
        raise HTTPException(status_code=403, detail='conversation_id required')

    q = q.order_by(Case.created_at.desc()).limit(100)
    rows = (await db.execute(q)).scalars().all()
    return {'items': [_case_payload(c) for c in rows]}


@router.get('/{case_id}')
async def get_case(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = await require_case_access(db, actor, case_id)
    return _case_payload(c)


@router.patch('/{case_id}')
async def patch_case(
    case_id: str,
    body: dict,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    c = await require_case_access(db, actor, case_id)

    changed = {}
    scalar_fields = [
        'status',
        'notes',
        'summary_public',
        'priority',
        'sla_deadline',
        'customer_ref_masked',
        'card_ref_masked',
        'operation_ref',
        'dispute_reason',
        'decision_summary',
    ]
    for field in scalar_fields:
        if field in body:
            setattr(c, field, None if body[field] is None else str(body[field]))
            changed[field] = True

    if 'facts_confirmed' in body:
        c.facts_confirmed_json = json.dumps(body['facts_confirmed'], ensure_ascii=False)
        changed['facts_confirmed'] = True
    if 'facts_pending' in body:
        c.facts_pending_json = json.dumps(body['facts_pending'], ensure_ascii=False)
        changed['facts_pending'] = True

    c.updated_at = datetime.utcnow()
    db.add(c)

    if changed:
        tl = CaseTimeline(case_id=c.id, kind='case_updated', payload=json.dumps(changed, ensure_ascii=False))
        db.add(tl)

    await db.commit()
    return {'ok': True, 'case': _case_payload(c)}


@router.get('/{case_id}/timeline')
async def timeline(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = await require_case_access(db, actor, case_id)
    rows = (
        await db.execute(
            select(CaseTimeline).where(CaseTimeline.case_id == c.id).order_by(CaseTimeline.id.asc())
        )
    ).scalars().all()
    return {
        'items': [
            {'id': t.id, 'kind': t.kind, 'payload': json.loads(t.payload) if t.payload else {}, 'created_at': t.created_at.isoformat()}
            for t in rows
        ]
    }
