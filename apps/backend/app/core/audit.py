from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.models import AuditEvent
from libs.common.kafka_bus import kafka_bus


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
):
    ev = AuditEvent(
        trace_id=trace_id,
        actor_role=actor_role,
        actor_id=actor_id,
        conversation_id=conversation_id,
        case_id=case_id,
        event_type=event_type,
        payload=json.dumps(payload, ensure_ascii=False),
    )
    db.add(ev)
    await db.commit()

    await kafka_bus.publish('copilot.audit.v1', {
        'trace_id': trace_id,
        'actor_role': actor_role,
        'actor_id': actor_id,
        'conversation_id': conversation_id,
        'case_id': case_id,
        'event_type': event_type,
        'payload': payload,
    })
