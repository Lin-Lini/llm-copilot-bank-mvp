from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import (
    SuggestRequest, SuggestCreated, SuggestStatusOut, TaskStatus,
    ExecuteToolRequest, ExecuteToolResponse,
    StateRequest, CopilotState,
    ProfileConfirmRequest, ProfileConfirmResponse,
    ToolExecuteRequest, ToolExecuteResponse,
    DraftV1, Intent, Phase, Plan,
)

from services.backend.app.audit import add_audit
from services.backend.app.deps import get_db
from shared.config import settings
# Use asynchronous LLM client instead of stub.  If no external URL is configured,
# the client falls back to the deterministic stub implementation.
from shared.llm_client import explain as llm_explain
from shared.plan_utils import reduce_plan_after_tool
from shared.moderator import moderate_output
from shared.policy import build_plan, allowed_tools
from shared.redis_client import get_redis
from shared.security import require_operator
from shared.models import Case, CaseProfileField, CaseTimeline


router = APIRouter(prefix='/copilot', tags=['copilot'])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(x_request_id: str | None) -> str:
    return x_request_id or str(uuid.uuid4())


def _task_key(task_id: str) -> str:
    return f'copilot:task:{task_id}'


def _task_result_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:result'


def _task_cancel_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:cancel'


def _stream_chan(task_id: str) -> str:
    return f'copilot:stream:{task_id}'


def _state_key(conversation_id: str) -> str:
    return f'copilot:state:{conversation_id}'


def _analyze_cache_key(conversation_id: str, last_message_id: int) -> str:
    return f'copilot:cache:analyze:{conversation_id}:{last_message_id}'


def _draft_cache_key(conversation_id: str, last_message_id: int) -> str:
    return f'copilot:cache:draft:{conversation_id}:{last_message_id}'


@router.post('/suggest', status_code=202, response_model=SuggestCreated)
async def suggest(
    req: SuggestRequest,
    actor=Depends(require_operator),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    r = get_redis()
    task_id = str(uuid.uuid4())
    trace_id = _trace(x_request_id)

    meta = {
        'task_id': task_id,
        'status': TaskStatus.queued.value,
        # use conversation_id from the request to avoid referencing an undefined local
        'conversation_id': req.conversation_id,
        'created_at': _now(),
        'updated_at': _now(),
        'error': None,
        'trace_id': trace_id,
        'max_messages': req.max_messages,
        'actor_id': actor['id'],
    }
    await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
    await r.rpush('copilot:queue:suggest', task_id)

    # для стрима: отметим создание
    await r.publish(_stream_chan(task_id), json.dumps({'event': 'status', 'data': meta}, ensure_ascii=False))

    return SuggestCreated(task_id=task_id)


@router.get('/suggest/{task_id}', response_model=SuggestStatusOut)
async def suggest_status(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        raise HTTPException(status_code=404, detail='task not found')
    meta = json.loads(raw)

    out = {
        'task_id': task_id,
        'status': meta.get('status'),
        'error': meta.get('error'),
        'result': None,
    }
    if meta.get('status') == TaskStatus.succeeded.value:
        res_raw = await r.get(_task_result_key(task_id))
        if res_raw:
            out['result'] = json.loads(res_raw)

    # валидируем через pydantic
    if out['result'] is not None:
        out['result'] = DraftV1.model_validate(out['result'])

    return SuggestStatusOut.model_validate(out)


@router.post('/suggest/{task_id}/cancel')
async def cancel(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    await r.set(_task_cancel_key(task_id), '1', ex=3600)
    return {'ok': True}


@router.get('/suggest/{task_id}/stream')
async def suggest_stream(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        raise HTTPException(status_code=404, detail='task not found')

    pubsub = r.pubsub()
    await pubsub.subscribe(_stream_chan(task_id))

    async def gen():
        try:
            # первая отдача статуса
            meta = json.loads(raw)
            yield {'event': 'status', 'data': meta}

            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10.0)
                if msg is None:
                    yield {'event': 'ping', 'data': {'t': _now()}}
                    continue
                data = msg.get('data')
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    obj = {'event': 'raw', 'data': str(data)}
                yield {'event': obj.get('event', 'event'), 'data': obj.get('data', obj)}
        finally:
            try:
                await pubsub.unsubscribe(_stream_chan(task_id))
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass

    return EventSourceResponse(gen())


@router.get('/state', response_model=CopilotState)
async def state(conversation_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_state_key(conversation_id))
    if not raw:
        # дефолтное состояние
        plan = build_plan(Intent.Unknown)
        return CopilotState(
            conversation_id=conversation_id,
            intent=Intent.Unknown,
            phase=Phase.Collect,
            plan=plan,
            last_analyze=None,
            last_draft=None,
        )

    obj = json.loads(raw)
    return CopilotState.model_validate(obj)


@router.post('/tools/execute', response_model=ExecuteToolResponse)
async def execute_tool(
    req: ExecuteToolRequest,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    r = get_redis()
    trace_id = _trace(x_request_id)

    state_raw = await r.get(_state_key(req.conversation_id))
    if not state_raw:
        raise HTTPException(status_code=409, detail='no copilot state for conversation')
    st = json.loads(state_raw)

    intent = st['intent']
    phase = st['phase']
    plan = st['plan']

    # policy-pack: allowlist
    tools_ui = allowed_tools(Intent(intent), Phase(phase))
    # derive missing_fields from last_analyze to enforce dynamic enablement
    missing_fields: list[str] = []
    if st.get('last_analyze') and isinstance(st['last_analyze'], dict):
        missing_fields = st['last_analyze'].get('missing_fields') or []
    # dynamic enablement: copy tool definitions and override enabled/reason based on missing_fields
    dynamic_allow: dict[str, Any] = {}
    for _t in tools_ui:
        t = _t
        # disable get_transactions until required confirmations are provided
        if t.tool.value == 'get_transactions' and any(
            mf in ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'] for mf in missing_fields
        ):
            t = t.model_copy(
                update={
                    'enabled': False,
                    'reason': 'Нужно уточнить наличие карты, сумму и время операции.',
                }
            )
        # disable block_card until customer confirmation is given
        if t.tool.value == 'block_card' and 'customer_confirm_block' in missing_fields:
            t = t.model_copy(
                update={
                    'enabled': False,
                    'reason': 'Нужно подтверждение клиента.',
                }
            )
        dynamic_allow[t.tool.value] = t

    allow = dynamic_allow
    ui = allow.get(req.tool.value)
    if ui is None:
        raise HTTPException(status_code=403, detail='tool not allowed by policy-pack')

    # если tool disabled, то только create_case допускаем в Collect
    if not ui.enabled and req.tool.value != 'create_case':
        raise HTTPException(status_code=409, detail=f'tool disabled: {ui.reason}')

    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        conversation_id=req.conversation_id,
        event_type='tool_called',
        payload={'tool': req.tool.value, 'idempotency_key': req.idempotency_key, 'params': req.params},
    )

    params = dict(req.params)
    params['conversation_id'] = req.conversation_id

    payload = ToolExecuteRequest(
        tool=req.tool,
        params=params,
        idempotency_key=req.idempotency_key,
        actor_role=actor['role'],
        actor_id=actor['id'],
        trace_id=trace_id,
    ).model_dump()

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f'{settings.mcp_tools_url}/api/v1/tools/execute',
            headers={
                'X-Internal-Auth': settings.internal_auth_token,
                'X-Actor-Role': actor['role'],
                'X-Actor-Id': actor['id'],
                'X-Request-Id': trace_id,
            },
            json=payload,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f'mcp-tools error: {resp.text}')

    tool_resp = ToolExecuteResponse.model_validate(resp.json())

    # EXPLAIN (async) + output moderation
    # Use the asynchronous LLM client; if no external URL is configured, this
    # falls back to the stub implementation.  The result is a pydantic
    # ExplainV1 instance.
    exp = await llm_explain(req.tool.value, tool_resp.result, Plan.model_validate(plan))
    mod = moderate_output(exp.ghost_text)
    if not mod['ok']:
        exp = exp.model_copy(update={'ghost_text': 'Сделано. Для безопасности я не буду формулировать потенциально опасные инструкции. Дальше можно проверить статус обращения или уточнить дополнительные данные.'})

    # state update.  Start with the plan from the previous state and apply
    # deterministic reducer based on the executed tool.  Then merge with
    # any updates returned from the LLM (if present).
    current_plan = Plan.model_validate(plan)
    # apply deterministic reduction for the executed tool
    deterministic_plan = reduce_plan_after_tool(current_plan, req.tool.value)
    # use the phase returned by the LLM if present; otherwise default to Explain
    new_phase = exp.updates.phase.value if exp and exp.updates and exp.updates.phase else Phase.Explain.value
    # decide which plan to store: prefer the one from exp.updates if provided, otherwise deterministic
    try:
        plan_to_store = exp.updates.plan
    except Exception:
        plan_to_store = deterministic_plan
    new_state = {
        'conversation_id': req.conversation_id,
        'intent': intent,
        'phase': new_phase,
        'plan': plan_to_store.model_dump(),
        'last_analyze': st.get('last_analyze'),
        'last_draft': st.get('last_draft'),
    }
    await r.set(_state_key(req.conversation_id), json.dumps(new_state, ensure_ascii=False))

    # кейс/timeline
    case_id = None
    if 'case_id' in tool_resp.result:
        case_id = tool_resp.result.get('case_id')
    if case_id is None:
        # use req.conversation_id instead of undefined local
        c = (
            await db.execute(
                select(Case)
                .where(Case.conversation_id == req.conversation_id)
                .order_by(Case.created_at.desc())
            )
        ).scalars().first()
        if c:
            case_id = c.id

    if case_id:
        tl = CaseTimeline(case_id=case_id, kind='tool_result', payload=json.dumps({'tool': req.tool.value, 'result': tool_resp.result}, ensure_ascii=False))
        db.add(tl)
        await db.commit()

    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        conversation_id=req.conversation_id,
        case_id=case_id,
        event_type='tool_result',
        payload={'tool': req.tool.value, 'result': tool_resp.result},
    )

    return ExecuteToolResponse(tool=req.tool, result=tool_resp.result, explain=exp)


@router.post('/profile/confirm', response_model=ProfileConfirmResponse)
async def profile_confirm(
    req: ProfileConfirmRequest,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    trace_id = req.trace_id or _trace(x_request_id)

    case_id = req.case_id
    if case_id is None:
        # use req.conversation_id instead of undefined local for case lookup
        c = (
            await db.execute(
                select(Case)
                .where(Case.conversation_id == req.conversation_id)
                .order_by(Case.created_at.desc())
            )
        ).scalars().first()
        if c:
            case_id = c.id

    if case_id is None:
        return ProfileConfirmResponse(stored=0)

    stored = 0
    for f in req.fields:
        row = CaseProfileField(case_id=case_id, field_name=f.field_name, value=f.value, trace_id=trace_id, confirmed_by=actor['id'])
        db.add(row)
        stored += 1

    if stored:
        tl = CaseTimeline(case_id=case_id, kind='profile_confirmed', payload=json.dumps({'stored': stored}, ensure_ascii=False))
        db.add(tl)

    await db.commit()

    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        conversation_id=req.conversation_id,
        case_id=case_id,
        event_type='profile_confirmed',
        payload={'stored': stored, 'fields': [x.model_dump() for x in req.fields]},
    )

    return ProfileConfirmResponse(stored=stored)
