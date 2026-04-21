from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.audit_store import add_audit_event


async def add_audit(
    db: AsyncSession,
    *,
    trace_id: str,
    actor_role: str,
    actor_id: str,
    event_type: str,
    payload: dict[str, Any],
    conversation_id: str | None = None,
    case_id: str | None = None,
    retrieval_snapshot: list[dict[str, Any]] | None = None,
    state_before: dict[str, Any] | None = None,
    state_after: dict[str, Any] | None = None,
    prompt_hash: str | None = None,
    policy_version: str | None = None,
    cache_info: dict[str, Any] | None = None,
):
    await add_audit_event(
        db,
        trace_id=trace_id,
        actor_role=actor_role,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload,
        conversation_id=conversation_id,
        case_id=case_id,
        retrieval_snapshot=retrieval_snapshot,
        state_before=state_before,
        state_after=state_after,
        prompt_hash=prompt_hash,
        policy_version=policy_version,
        cache_info=cache_info,
    )