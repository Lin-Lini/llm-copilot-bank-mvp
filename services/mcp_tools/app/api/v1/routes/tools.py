from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException

from contracts.schemas import ToolExecuteRequest, ToolExecuteResponse, ToolName, InternalCreateCaseRequest
from shared.config import settings
from shared.kafka_bus import kafka_bus
from shared.redis_client import get_redis
from shared.security import require_operator


router = APIRouter(prefix='/tools', tags=['tools'])


def _idem_key(tool: str, key: str) -> str:
    return f'mcp:idem:{tool}:{key}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _backend_post(path: str, actor: dict, trace_id: str, json_body: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f'http://backend:8080{path}',
            headers={
                'X-Internal-Auth': settings.internal_auth_token,
                'X-Actor-Role': actor['role'],
                'X-Actor-Id': actor['id'],
                'X-Request-Id': trace_id,
            },
            json=json_body,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f'backend internal error: {r.text}')
    return r.json()


async def _backend_get(path: str, actor: dict, trace_id: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f'http://backend:8080{path}',
            headers={
                'X-Internal-Auth': settings.internal_auth_token,
                'X-Actor-Role': actor['role'],
                'X-Actor-Id': actor['id'],
                'X-Request-Id': trace_id,
            },
            params=params,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f'backend internal error: {r.text}')
    return r.json()


@router.post('/execute', response_model=ToolExecuteResponse)
async def execute(
    req: ToolExecuteRequest,
    actor=Depends(require_operator),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    r = get_redis()
    tool = req.tool.value
    idem_key = _idem_key(tool, req.idempotency_key)

    cached = await r.get(idem_key)
    if cached:
        return ToolExecuteResponse.model_validate(json.loads(cached))

    trace_id = x_request_id or req.trace_id or str(uuid.uuid4())

    await kafka_bus.publish('copilot.tools.v1', {
        'event': 'tool_called',
        'tool': tool,
        'trace_id': trace_id,
        'actor_id': actor['id'],
        't': _now(),
        'idempotency_key': req.idempotency_key,
    })

    result: dict[str, Any]

    if req.tool == ToolName.create_case:
        summary = str(req.params.get('summary_public') or 'Обращение создано')
        intent = req.params.get('intent') or 'Unknown'
        conv_id = str(req.params.get('conversation_id') or '')
        if not conv_id:
            # в orchestrator conv_id лежит в req.params не всегда, поэтому позволим передать его отдельно через params
            raise HTTPException(status_code=400, detail='conversation_id required in params for create_case')

        body = InternalCreateCaseRequest(conversation_id=conv_id, summary_public=summary, intent=intent).model_dump()
        result = await _backend_post('/api/v1/_internal/cases/create', actor, trace_id, body)

    elif req.tool == ToolName.get_case_status:
        case_id = str(req.params.get('case_id') or '')
        if not case_id:
            raise HTTPException(status_code=400, detail='case_id required')
        result = await _backend_get('/api/v1/_internal/cases/status', actor, trace_id, {'case_id': case_id})

    elif req.tool == ToolName.get_transactions:
        # mock
        result = {
            'transactions': [
                {'txn_id': 'txn-001', 'date': '2026-02-16', 'amount': 1290, 'currency': 'RUB', 'merchant': '<masked_merchant>'},
                {'txn_id': 'txn-002', 'date': '2026-02-15', 'amount': 499, 'currency': 'RUB', 'merchant': '<masked_merchant>'},
            ]
        }

    elif req.tool == ToolName.block_card:
        result = {'blocked': True, 'reference_id': f'ref-{uuid.uuid4().hex[:8]}'}

    elif req.tool == ToolName.unblock_card:
        result = {'unblocked': True, 'reference_id': f'ref-{uuid.uuid4().hex[:8]}'}

    elif req.tool == ToolName.reissue_card:
        result = {'order_id': f'ord-{uuid.uuid4().hex[:8]}', 'eta_days': 5, 'reference_id': f'ref-{uuid.uuid4().hex[:8]}'}

    elif req.tool == ToolName.get_card_limits:
        result = {'limits': {'online': 50000, 'atm': 20000, 'pos': 80000}, 'enabled_flags': {'online_payments': True}}

    elif req.tool == ToolName.set_card_limits:
        limits = req.params.get('limits') or {}
        result = {'applied': True, 'limits': limits}

    elif req.tool == ToolName.toggle_online_payments:
        enabled = bool(req.params.get('enabled'))
        result = {'applied': True, 'enabled': enabled}

    else:
        raise HTTPException(status_code=400, detail='unknown tool')

    resp = ToolExecuteResponse(tool=req.tool, result=result)

    await kafka_bus.publish('copilot.tools.v1', {
        'event': 'tool_result',
        'tool': tool,
        'trace_id': trace_id,
        'actor_id': actor['id'],
        't': _now(),
        'result': result,
    })

    await r.set(idem_key, json.dumps(resp.model_dump(), ensure_ascii=False), ex=3600)
    return resp
