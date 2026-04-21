from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from libs.common.kafka_bus import kafka_bus
from libs.common.models import AuditEvent
from libs.common.policy_meta import POLICY_VERSION


def _normalize(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, 'model_dump'):
        return _normalize(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(v) for v in value]
    return str(value)


async def add_audit_event(
    db,
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
    payload_n = _normalize(payload) or {}
    retrieval_n = _normalize(retrieval_snapshot) if retrieval_snapshot is not None else None
    before_n = _normalize(state_before) if state_before is not None else None
    after_n = _normalize(state_after) if state_after is not None else None
    cache_n = _normalize(cache_info) if cache_info is not None else None

    ev = AuditEvent(
        trace_id=trace_id,
        actor_role=actor_role,
        actor_id=actor_id,
        conversation_id=conversation_id,
        case_id=case_id,
        event_type=event_type,
        payload=json.dumps(payload_n, ensure_ascii=False),
        payload_json=payload_n,
        retrieval_snapshot_json=retrieval_n,
        state_before_json=before_n,
        state_after_json=after_n,
        cache_info_json=cache_n,
        prompt_hash=prompt_hash,
        policy_version=policy_version or POLICY_VERSION,
    )
    db.add(ev)
    await db.commit()

    await kafka_bus.publish(
        'copilot.audit.v1',
        {
            'trace_id': trace_id,
            'actor_role': actor_role,
            'actor_id': actor_id,
            'conversation_id': conversation_id,
            'case_id': case_id,
            'event_type': event_type,
            'payload': payload_n,
            'retrieval_snapshot': retrieval_n,
            'state_before': before_n,
            'state_after': after_n,
            'cache_info': cache_n,
            'prompt_hash': prompt_hash,
            'policy_version': policy_version or POLICY_VERSION,
        },
    )