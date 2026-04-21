from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.deps import get_db
from libs.common.models import AuditEvent
from libs.common.security import require_operator


router = APIRouter(prefix='/audit', tags=['audit'])


def _payload(r: AuditEvent):
    if r.payload_json is not None:
        return r.payload_json
    if r.payload:
        try:
            return json.loads(r.payload)
        except Exception:
            return {'raw': r.payload}
    return {}


def _row(r: AuditEvent):
    return {
        'id': r.id,
        'created_at': r.created_at.isoformat(),
        'trace_id': r.trace_id,
        'actor_role': r.actor_role,
        'actor_id': r.actor_id,
        'conversation_id': r.conversation_id,
        'case_id': r.case_id,
        'event_type': r.event_type,
        'payload': _payload(r),
        'retrieval_snapshot': r.retrieval_snapshot_json,
        'state_before': r.state_before_json,
        'state_after': r.state_after_json,
        'cache_info': r.cache_info_json,
        'prompt_hash': r.prompt_hash,
        'policy_version': r.policy_version,
    }


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
    return {'items': [_row(r) for r in rows]}


@router.get('/trace/{trace_id}')
async def trace(trace_id: str, actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.id.asc())
        )
    ).scalars().all()
    return {'items': [_row(r) for r in rows]}


@router.get('/trace/{trace_id}/replay')
async def trace_replay(trace_id: str, actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.id.asc())
        )
    ).scalars().all()

    latest_state = None
    latest_retrieval = None
    latest_cache = None
    prompt_hashes: list[str] = []
    policy_versions: list[str] = []

    for r in rows:
        if r.state_after_json is not None:
            latest_state = r.state_after_json
        elif latest_state is None and r.state_before_json is not None:
            latest_state = r.state_before_json

        if r.retrieval_snapshot_json is not None:
            latest_retrieval = r.retrieval_snapshot_json

        if r.cache_info_json is not None:
            latest_cache = r.cache_info_json

        if r.prompt_hash and r.prompt_hash not in prompt_hashes:
            prompt_hashes.append(r.prompt_hash)

        if r.policy_version and r.policy_version not in policy_versions:
            policy_versions.append(r.policy_version)

    return {
        'trace_id': trace_id,
        'events_count': len(rows),
        'policy_versions': policy_versions,
        'prompt_hashes': prompt_hashes[:20],
        'current_state': latest_state,
        'retrieval_snapshot': latest_retrieval,
        'cache_info': latest_cache,
        'events': [_row(r) for r in rows],
    }


@router.get('/trace/{trace_id}/export')
async def trace_export(trace_id: str, actor=Depends(require_operator), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.id.asc())
        )
    ).scalars().all()

    items = [_row(r) for r in rows]
    return {
        'trace_id': trace_id,
        'events_count': len(items),
        'items': items,
    }