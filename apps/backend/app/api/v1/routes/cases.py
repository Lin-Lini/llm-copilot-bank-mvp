from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.access import require_case_access, require_conversation_access
from apps.backend.app.core.deps import get_db
from libs.common.case_dossier import build_analyze_from_case_context
from libs.common.case_dossier_store import get_case_dossier_payload
from libs.common.case_readiness import build_missing_field_meta, build_readiness, infer_case_phase, normalize_intent
from libs.common.json_lists import normalize_string_list, parse_string_list
from libs.common.models import Case, CaseTimeline
from libs.common.security import require_actor, require_operator
from libs.common.state_engine import resolve_tools

router = APIRouter(prefix='/cases', tags=['cases'])


async def _load_timeline(db: AsyncSession, case_id: str) -> list[CaseTimeline]:
    return (
        await db.execute(
            select(CaseTimeline).where(CaseTimeline.case_id == case_id).order_by(CaseTimeline.id.asc())
        )
    ).scalars().all()


def _timeline_payload(changed: dict, case_obj: Case) -> dict:
    payload = {'changed_fields': sorted(changed.keys())}
    for field in ['status', 'notes', 'summary_public', 'priority', 'sla_deadline', 'customer_ref_masked', 'card_ref_masked', 'operation_ref', 'dispute_reason', 'decision_summary']:
        if field in changed:
            payload[field] = getattr(case_obj, field)
    if 'facts_confirmed' in changed:
        payload['facts_confirmed'] = parse_string_list(case_obj.facts_confirmed_json)
    if 'facts_pending' in changed:
        payload['facts_pending'] = parse_string_list(case_obj.facts_pending_json)
    return payload


async def _case_payload(
    db: AsyncSession,
    c: Case,
    *,
    timeline_rows: list[CaseTimeline] | None = None,
    include_dossier: bool = False,
) -> dict:
    if timeline_rows is None:
        timeline_rows = await _load_timeline(db, c.id)

    intent = normalize_intent(c.case_type)
    facts_pending = parse_string_list(c.facts_pending_json)
    facts_confirmed = parse_string_list(c.facts_confirmed_json)
    analyze = build_analyze_from_case_context(c, timeline_rows)

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

    ui_missing_fields = [] if readiness.status.value == 'completed' else facts_pending

    payload = {
        'case_id': c.id,
        'conversation_id': c.conversation_id,
        'case_type': c.case_type,
        'intent': intent.value,
        'phase': phase.value,
        'priority': c.priority,
        'sla_deadline': c.sla_deadline,
        'customer_ref_masked': c.customer_ref_masked,
        'card_ref_masked': c.card_ref_masked,
        'operation_ref': c.operation_ref,
        'dispute_reason': c.dispute_reason,
        'facts_confirmed': facts_confirmed,
        'facts_pending': facts_pending,
        'missing_fields_meta': build_missing_field_meta(intent, ui_missing_fields, analyze),
        'decision_summary': c.decision_summary,
        'status': c.status,
        'summary_public': c.summary_public,
        'notes': c.notes,
        'readiness': readiness.model_dump(),
        'created_at': c.created_at.isoformat(),
        'updated_at': c.updated_at.isoformat(),
    }

    if include_dossier:
        payload['dossier'] = await get_case_dossier_payload(
            db,
            c,
            timeline_rows=timeline_rows,
        )

    return payload


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
    return {'items': [await _case_payload(db, c) for c in rows]}


@router.get('/{case_id}')
async def get_case(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = await require_case_access(db, actor, case_id)
    timeline_rows = await _load_timeline(db, c.id)
    return await _case_payload(db, c, timeline_rows=timeline_rows, include_dossier=True)


@router.get('/{case_id}/dossier')
async def get_case_dossier(
    case_id: str,
    refresh: bool = False,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    c = await require_case_access(db, actor, case_id)
    timeline_rows = await _load_timeline(db, c.id)
    return await get_case_dossier_payload(
        db,
        c,
        timeline_rows=timeline_rows,
        force_refresh=refresh,
    )


@router.patch('/{case_id}')
async def patch_case(
    case_id: str,
    body: dict,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    c = await require_case_access(db, actor, case_id)

    changed: dict[str, bool] = {}
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
        c.facts_confirmed_json = normalize_string_list(body['facts_confirmed'])
        changed['facts_confirmed'] = True

    if 'facts_pending' in body:
        c.facts_pending_json = normalize_string_list(body['facts_pending'])
        changed['facts_pending'] = True

    c.updated_at = datetime.now(timezone.utc)
    db.add(c)

    if changed:
        payload = _timeline_payload(changed, c)
        tl = CaseTimeline(
            case_id=c.id,
            kind='case_updated',
            payload=json.dumps(payload, ensure_ascii=False),
            payload_json=payload,
        )
        db.add(tl)

    await db.commit()

    timeline_rows = await _load_timeline(db, c.id)
    return {'ok': True, 'case': await _case_payload(db, c, timeline_rows=timeline_rows, include_dossier=True)}


@router.get('/{case_id}/timeline')
async def timeline(case_id: str, actor=Depends(require_actor), db: AsyncSession = Depends(get_db)):
    c = await require_case_access(db, actor, case_id)
    rows = await _load_timeline(db, c.id)
    return {
        'items': [
            {
                'id': t.id,
                'kind': t.kind,
                'payload': t.payload_json if t.payload_json is not None else (json.loads(t.payload) if t.payload else {}),
                'created_at': t.created_at.isoformat(),
            }
            for t in rows
        ]
    }