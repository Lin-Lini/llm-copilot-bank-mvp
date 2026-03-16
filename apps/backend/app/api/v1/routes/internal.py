from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import InternalCreateCaseRequest, Intent
from apps.backend.app.core.deps import get_db
from libs.common.models import Case, CaseTimeline
from libs.common.security import require_service


router = APIRouter(prefix='/_internal', tags=['internal'])


def _default_pending(intent: Intent) -> list[str]:
    if intent == Intent.SuspiciousTransaction:
        return [
            'card_in_possession',
            'txn_amount_confirm',
            'txn_datetime_confirm',
            'customer_confirm_block',
        ]
    return []


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
    c = Case(
        conversation_id=req.conversation_id,
        case_type=req.intent.value,
        priority=_priority(req.intent),
        sla_deadline=sla_deadline,
        dispute_reason=req.intent.value if req.intent == Intent.SuspiciousTransaction else '',
        facts_pending_json=json.dumps(_default_pending(req.intent), ensure_ascii=False),
        status='open',
        summary_public=req.summary_public,
    )
    db.add(c)
    await db.commit()

    tl = CaseTimeline(
        case_id=c.id,
        kind='case_created',
        payload=json.dumps(
            {
                'intent': req.intent.value,
                'priority': c.priority,
                'sla_deadline': c.sla_deadline,
            },
            ensure_ascii=False,
        ),
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
    return {
        'case_id': c.id,
        'status': c.status,
        'case_type': c.case_type,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'timeline': [
            {'id': t.id, 'kind': t.kind, 'payload': json.loads(t.payload) if t.payload else {}, 'created_at': t.created_at.isoformat()}
            for t in tl
        ],
    }
