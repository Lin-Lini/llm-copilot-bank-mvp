from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.app.core.access import require_conversation_access
from apps.backend.app.core.audit import add_audit
from apps.backend.app.core.bus import chat_bus
from apps.backend.app.core.deps import get_db
from libs.common.db import SessionLocal
from libs.common.kafka_bus import kafka_bus
from libs.common.models import Conversation, Message
from libs.common.security import require_actor
from libs.common.config import settings


router = APIRouter(prefix='/chat', tags=['chat'])


def _trace(x_request_id: str | None) -> str:
    return x_request_id or str(uuid.uuid4())


@router.post('/conversations')
async def create_conversation(
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    c = Conversation(owner_actor_role=actor['role'], owner_actor_id=actor['id'])
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
    await require_conversation_access(db, actor, conversation_id)

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
    await require_conversation_access(db, actor, conversation_id)

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

    await chat_bus.publish(conversation_id, event)
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
async def stream(
    conversation_id: str,
    actor=Depends(require_actor),
    db: AsyncSession = Depends(get_db),
):
    await require_conversation_access(db, actor, conversation_id)
    q = await chat_bus.subscribe(conversation_id)

    async def gen():
        try:
            while True:
                ev = await q.get()
                yield {'event': ev.get('type', 'event'), 'data': ev}
        finally:
            await chat_bus.unsubscribe(conversation_id, q)

    return EventSourceResponse(gen())


@router.websocket('/ws')
async def ws(ws: WebSocket):
    q = None
    conversation_id = ws.query_params.get('conversation_id') or ''
    try:
        token = ws.headers.get('x-internal-auth')
        role = ws.headers.get('x-actor-role')
        aid = ws.headers.get('x-actor-id')
        if token != settings.internal_auth_token or not role or not aid:
            await ws.close(code=4401)
            return
        if not conversation_id:
            await ws.close(code=4400)
            return

        actor = {'role': role, 'id': aid}
        async with SessionLocal() as db:
            try:
                await require_conversation_access(db, actor, conversation_id)
            except HTTPException:
                await ws.close(code=4403)
                return

        await ws.accept()
        q = await chat_bus.subscribe(conversation_id)
        while True:
            ev = await q.get()
            await ws.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        if q is not None:
            await chat_bus.unsubscribe(conversation_id, q)
