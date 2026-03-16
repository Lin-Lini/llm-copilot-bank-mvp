from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.app.bus import chat_bus
from services.backend.app.deps import get_db
from services.backend.app.audit import add_audit
from shared.kafka_bus import kafka_bus
from shared.models import Conversation, Message
from shared.security import require_actor


router = APIRouter(prefix='/chat', tags=['chat'])


def _trace(x_request_id: str | None) -> str:
    return x_request_id or str(uuid.uuid4())


@router.post('/conversations')
async def create_conversation(
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    c = Conversation()
    db.add(c)
    await db.commit()

    trace_id = _trace(x_request_id)
    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        event_type='conversation_created',
        payload={'conversation_id': c.id},
        conversation_id=c.id,
    )

    return {'conversation_id': c.id}


@router.get('/conversations/{conversation_id}/messages')
async def get_messages(
    conversation_id: str,
    limit: int = 50,
    before_id: int | None = None,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    q = select(Message).where(Message.conversation_id == conversation_id)
    if before_id is not None:
        q = q.where(Message.id < before_id)
    q = q.order_by(Message.id.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    rows = list(reversed(rows))
    return {
        'items': [
            {
                'id': m.id,
                'actor_role': m.actor_role,
                'actor_id': m.actor_id,
                'content': m.content,
                'created_at': m.created_at.isoformat(),
            }
            for m in rows
        ]
    }


@router.post('/conversations/{conversation_id}/messages')
async def post_message(
    conversation_id: str,
    body: dict[str, Any],
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    content = (body.get('content') or '').strip()
    if not content:
        return {'ok': False, 'error': 'empty content'}

    m = Message(conversation_id=conversation_id, actor_role=actor['role'], actor_id=actor['id'], content=content)
    db.add(m)
    await db.commit()

    trace_id = _trace(x_request_id)
    event = {
        'type': 'message_created',
        'conversation_id': conversation_id,
        'message': {
            'id': m.id,
            'actor_role': m.actor_role,
            'actor_id': m.actor_id,
            'content': m.content,
            'created_at': m.created_at.isoformat(),
        },
        'trace_id': trace_id,
    }

    await chat_bus.publish(event)
    await kafka_bus.publish('copilot.chat.v1', event)

    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        event_type='message_created',
        payload={'message_id': m.id},
        conversation_id=conversation_id,
    )

    return {'id': m.id}


@router.get('/stream')
async def stream(actor=Depends(require_actor)):
    q = await chat_bus.subscribe()

    async def gen():
        try:
            while True:
                ev = await q.get()
                yield {'event': ev.get('type', 'event'), 'data': ev}
        finally:
            await chat_bus.unsubscribe(q)

    return EventSourceResponse(gen())


@router.websocket('/ws')
async def ws(ws: WebSocket):
    # internal auth for WS: headers still available
    try:
        token = ws.headers.get('x-internal-auth')
        role = ws.headers.get('x-actor-role')
        aid = ws.headers.get('x-actor-id')
        from shared.config import settings
        if token != settings.internal_auth_token or not role or not aid:
            await ws.close(code=4401)
            return

        await ws.accept()
        q = await chat_bus.subscribe()
        while True:
            ev = await q.get()
            await ws.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await chat_bus.unsubscribe(q)
        except Exception:
            pass
