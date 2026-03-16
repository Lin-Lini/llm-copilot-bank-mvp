from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.app.deps import get_db
from shared.models import Case, CaseTimeline
from shared.security import require_actor


router = APIRouter(prefix='/cases', tags=['cases'])


@router.get('')
async def list_cases(
    conversation_id: str | None = None,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    q = select(Case)
    if conversation_id:
        q = q.where(Case.conversation_id == conversation_id)
    q = q.order_by(Case.created_at.desc()).limit(100)
    rows = (await db.execute(q)).scalars().all()
    return {'items': [
        {
            'case_id': c.id,
            'conversation_id': c.conversation_id,
            'status': c.status,
            'summary_public': c.summary_public,
            'updated_at': c.updated_at.isoformat(),
        } for c in rows
    ]}


@router.get('/{case_id}')
async def get_case(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not c:
        return {'error': 'not_found'}
    return {
        'case_id': c.id,
        'conversation_id': c.conversation_id,
        'status': c.status,
        'summary_public': c.summary_public,
        'notes': c.notes,
        'created_at': c.created_at.isoformat(),
        'updated_at': c.updated_at.isoformat(),
    }


@router.patch('/{case_id}')
async def patch_case(case_id: str, body: dict, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not c:
        return {'error': 'not_found'}

    changed = {}
    if 'status' in body:
        c.status = str(body['status'])
        changed['status'] = c.status
    if 'notes' in body:
        c.notes = str(body['notes'])
        changed['notes'] = True

    from datetime import datetime
    c.updated_at = datetime.utcnow()
    db.add(c)

    if changed:
        tl = CaseTimeline(case_id=c.id, kind='case_updated', payload=json.dumps(changed, ensure_ascii=False))
        db.add(tl)

    await db.commit()
    return {'ok': True}


@router.get('/{case_id}/timeline')
async def timeline(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CaseTimeline).where(CaseTimeline.case_id == case_id).order_by(CaseTimeline.id.asc()))).scalars().all()
    return {'items': [
        {'id': t.id, 'kind': t.kind, 'payload': t.payload, 'created_at': t.created_at.isoformat()}
        for t in rows
    ]}
