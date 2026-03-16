from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.deps import get_db
from libs.common.models import AuditEvent
from libs.common.security import require_operator


router = APIRouter(prefix='/audit', tags=['audit'])


@router.get('')
async def search_audit(
    conversation_id: str | None = None,
    case_id: str | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    q = select(AuditEvent)
    if conversation_id:
        q = q.where(AuditEvent.conversation_id == conversation_id)
    if case_id:
        q = q.where(AuditEvent.case_id == case_id)
    if trace_id:
        q = q.where(AuditEvent.trace_id == trace_id)

    q = q.order_by(AuditEvent.id.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()

    return {
        'items': [
            {
                'id': r.id,
                'created_at': r.created_at.isoformat(),
                'trace_id': r.trace_id,
                'actor_role': r.actor_role,
                'actor_id': r.actor_id,
                'conversation_id': r.conversation_id,
                'case_id': r.case_id,
                'event_type': r.event_type,
                'payload': json.loads(r.payload) if r.payload else {},
            }
            for r in rows
        ]
    }


@router.get('/trace/{trace_id}')
async def trace(trace_id: str, actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.id.asc())
        )
    ).scalars().all()
    return {
        'items': [
            {
                'id': r.id,
                'created_at': r.created_at.isoformat(),
                'event_type': r.event_type,
                'payload': json.loads(r.payload) if r.payload else {},
            }
            for r in rows
        ]
    }
