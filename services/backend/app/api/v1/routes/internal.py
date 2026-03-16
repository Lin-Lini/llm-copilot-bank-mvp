from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import InternalCreateCaseRequest
from services.backend.app.deps import get_db
from shared.models import Case, CaseTimeline
from shared.security import require_actor


router = APIRouter(prefix='/_internal', tags=['internal'])


@router.post('/cases/create')
async def internal_create_case(
    req: InternalCreateCaseRequest,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    c = Case(conversation_id=req.conversation_id, status='open', summary_public=req.summary_public)
    db.add(c)
    await db.commit()

    tl = CaseTimeline(case_id=c.id, kind='case_created', payload=json.dumps({'intent': req.intent.value}, ensure_ascii=False))
    db.add(tl)
    await db.commit()

    return {'case_id': c.id, 'status': c.status, 'created_at': c.created_at.isoformat()}


@router.get('/cases/status')
async def internal_case_status(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not c:
        return {'error': 'not_found'}
    tl = (await db.execute(select(CaseTimeline).where(CaseTimeline.case_id == case_id).order_by(CaseTimeline.id.asc()))).scalars().all()
    return {
        'case_id': c.id,
        'status': c.status,
        'timeline': [
            {'id': t.id, 'kind': t.kind, 'payload': t.payload, 'created_at': t.created_at.isoformat()}
            for t in tl
        ],
    }
