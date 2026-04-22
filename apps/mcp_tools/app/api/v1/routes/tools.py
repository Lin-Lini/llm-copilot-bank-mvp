from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException

from contracts.schemas import InternalCreateCaseRequest, ToolExecuteRequest, ToolExecuteResponse, ToolName
from libs.common.internal_auth import build_internal_headers
from libs.common.kafka_bus import kafka_bus
from libs.common.redis_client import get_redis
from libs.common.security import require_operator

router = APIRouter(prefix='/tools', tags=['tools'])

_IDEMPOTENCY_TTL_SEC = 3600


def _idem_scope(params: dict[str, Any], actor: dict) -> str:
    conv_id = str(params.get('conversation_id') or '')
    case_id = str(params.get('case_id') or '')
    return f"{actor['role']}:{actor['id']}:{conv_id}:{case_id}"


def _params_hash(params: dict[str, Any]) -> str:
    payload = json.dumps(params or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _idem_meta_key(tool: str, key: str, scope: str) -> str:
    scope = scope or '-'
    return f'mcp:idem-meta:{tool}:{scope}:{key}'


def _idem_result_key(tool: str, key: str, scope: str, params_hash: str) -> str:
    scope = scope or '-'
    return f'mcp:idem:{tool}:{scope}:{key}:{params_hash}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _backend_post(path: str, actor: dict, trace_id: str, json_body: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f'http://backend:8080{path}',
            headers=build_internal_headers(
                actor_role='service',
                actor_id='mcp-tools',
                request_id=trace_id,
                issuer='mcp-tools',
                origin_actor_role=actor['role'],
                origin_actor_id=actor['id'],
            ),
            json=json_body,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f'backend internal error: {r.text}')
    return r.json()


async def _backend_get(path: str, actor: dict, trace_id: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f'http://backend:8080{path}',
            headers=build_internal_headers(
                actor_role='service',
                actor_id='mcp-tools',
                request_id=trace_id,
                issuer='mcp-tools',
                origin_actor_role=actor['role'],
                origin_actor_id=actor['id'],
            ),
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
    scope = _idem_scope(req.params, actor)
    params_hash = _params_hash(req.params)
    meta_key = _idem_meta_key(tool, req.idempotency_key, scope)
    result_key = _idem_result_key(tool, req.idempotency_key, scope, params_hash)

    stored_params_hash = await r.get(meta_key)
    if stored_params_hash:
        if stored_params_hash != params_hash:
            raise HTTPException(status_code=409, detail='idempotency_key reused with different params')

        cached = await r.get(result_key)
        if cached:
            return ToolExecuteResponse.model_validate(json.loads(cached))

    trace_id = x_request_id or req.trace_id or str(uuid.uuid4())

    await kafka_bus.publish(
        'copilot.tools.v1',
        {
            'event': 'tool_called',
            'tool': tool,
            'trace_id': trace_id,
            'actor_id': actor['id'],
            't': _now(),
            'idempotency_key': req.idempotency_key,
            'params_hash': params_hash,
        },
    )

    result: dict[str, Any]

    if req.tool == ToolName.create_case:
        summary = str(req.params.get('summary_public') or 'Обращение создано')
        intent = req.params.get('intent') or 'Unknown'
        conv_id = str(req.params.get('conversation_id') or '')
        if not conv_id:
            raise HTTPException(status_code=400, detail='conversation_id required in params for create_case')

        body = InternalCreateCaseRequest(
            conversation_id=conv_id,
            summary_public=summary,
            intent=intent,
        ).model_dump()
        result = await _backend_post('/api/v1/_internal/cases/create', actor, trace_id, body)

    elif req.tool == ToolName.get_case_status:
        case_id = str(req.params.get('case_id') or '')
        if not case_id:
            raise HTTPException(status_code=400, detail='case_id required')
        result = await _backend_get('/api/v1/_internal/cases/status', actor, trace_id, {'case_id': case_id})

    elif req.tool == ToolName.get_transactions:
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

    await kafka_bus.publish(
        'copilot.tools.v1',
        {
            'event': 'tool_result',
            'tool': tool,
            'trace_id': trace_id,
            'actor_id': actor['id'],
            't': _now(),
            'result': result,
            'params_hash': params_hash,
        },
    )

    await r.set(meta_key, params_hash, ex=_IDEMPOTENCY_TTL_SEC)
    await r.set(result_key, json.dumps(resp.model_dump(), ensure_ascii=False), ex=_IDEMPOTENCY_TTL_SEC)
    return resp