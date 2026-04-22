from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.case_dossier import build_case_dossier
from libs.common.case_readiness import build_readiness, infer_case_phase, normalize_intent
from libs.common.models import Case, CaseDossierSnapshot, CaseTimeline
from libs.common.state_engine import resolve_tools
from libs.common.json_lists import parse_string_list

DOSSIER_SCHEMA_VERSION = '1.0'
TERMINAL_CASE_STATUSES = {'closed', 'resolved', 'done'}


def is_terminal_case_status(status: str | None) -> bool:
    return str(status or '').strip().lower() in TERMINAL_CASE_STATUSES


def _build_readiness_for_case(case_obj: Any):
    intent = normalize_intent(getattr(case_obj, 'case_type', None))
    facts_pending = parse_string_list(getattr(case_obj, 'facts_pending_json', None))
    phase = infer_case_phase(intent, facts_pending, getattr(case_obj, 'status', None))
    tools_ui = resolve_tools(intent, phase, missing_fields=facts_pending)
    return build_readiness(
        intent=intent,
        missing_fields=facts_pending,
        tools=tools_ui,
        case_status=getattr(case_obj, 'status', None),
    )


async def _load_timeline(db: AsyncSession, case_id: str) -> list[CaseTimeline]:
    return (
        await db.execute(
            select(CaseTimeline).where(CaseTimeline.case_id == case_id).order_by(CaseTimeline.id.asc())
        )
    ).scalars().all()


async def load_case_dossier_snapshot(db: AsyncSession, case_id: str) -> CaseDossierSnapshot | None:
    return (
        await db.execute(
            select(CaseDossierSnapshot).where(CaseDossierSnapshot.case_id == case_id)
        )
    ).scalar_one_or_none()


async def get_case_dossier_payload(
    db: AsyncSession,
    case_obj: Case | Any,
    *,
    timeline_rows: list[CaseTimeline] | None = None,
    force_refresh: bool = False,
    persist_if_terminal: bool = True,
) -> dict[str, Any]:
    case_id = str(getattr(case_obj, 'id'))
    current_status = str(getattr(case_obj, 'status', '') or 'open')

    if timeline_rows is None:
        timeline_rows = await _load_timeline(db, case_id)

    latest_timeline_event_id = timeline_rows[-1].id if timeline_rows else None

    snapshot = None
    if not force_refresh and is_terminal_case_status(current_status):
        snapshot = await load_case_dossier_snapshot(db, case_id)
        if (
            snapshot is not None
            and snapshot.schema_version == DOSSIER_SCHEMA_VERSION
            and snapshot.current_status == current_status
            and snapshot.built_from_timeline_event_id == latest_timeline_event_id
            and isinstance(snapshot.payload_json, dict)
        ):
            return snapshot.payload_json

    readiness = _build_readiness_for_case(case_obj)
    dossier = build_case_dossier(
        case_obj,
        readiness=readiness,
        timeline_rows=timeline_rows,
    )
    payload = dossier.model_dump()

    if persist_if_terminal and is_terminal_case_status(current_status):
        if snapshot is None:
            snapshot = await load_case_dossier_snapshot(db, case_id)

        now = datetime.now(timezone.utc)

        if snapshot is None:
            snapshot = CaseDossierSnapshot(
                case_id=case_id,
                schema_version=DOSSIER_SCHEMA_VERSION,
                current_status=current_status,
                built_from_timeline_event_id=latest_timeline_event_id,
                payload_json=payload,
                created_at=now,
                updated_at=now,
            )
        else:
            snapshot.schema_version = DOSSIER_SCHEMA_VERSION
            snapshot.current_status = current_status
            snapshot.built_from_timeline_event_id = latest_timeline_event_id
            snapshot.payload_json = payload
            snapshot.updated_at = now

        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

        if isinstance(snapshot.payload_json, dict):
            return snapshot.payload_json

    return payload