from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import (
    CopilotState,
    DraftV1,
    ExecuteToolRequest,
    ExecuteToolResponse,
    Intent,
    Phase,
    Plan,
    ProfileConfirmRequest,
    ProfileConfirmResponse,
    SuggestCreated,
    SuggestRequest,
    SuggestStatusOut,
    TaskStatus,
    ToolExecuteRequest,
    ToolExecuteResponse,
)
from apps.backend.app.core.access import require_conversation_access, require_task_access
from apps.backend.app.core.audit import add_audit
from apps.backend.app.core.deps import get_db
from libs.common.config import settings
from libs.common.internal_auth import build_internal_headers
from libs.common.llm_client import explain as llm_explain
from libs.common.llm_stub import explain as stub_explain
from libs.common.models import Case, CaseProfileField, CaseTimeline
from libs.common.moderator import moderate_output
from libs.common.policy_meta import POLICY_VERSION, make_prompt_hash
from libs.common.redis_client import get_redis
from libs.common.security import require_operator
from libs.common.state_engine import build_plan, phase_from_plan, reduce_plan_after_tool, resolve_tools

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


@router.post('/suggest', status_code=202, response_model=SuggestCreated)
async def suggest(
    req: SuggestRequest,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    await require_conversation_access(db, actor, req.conversation_id)

    r = get_redis()
    task_id = str(uuid.uuid4())
    trace_id = _trace(x_request_id)

    meta = {
        'task_id': task_id,
        'status': TaskStatus.queued.value,
        'conversation_id': req.conversation_id,
        'created_at': _now(),
        'updated_at': _now(),
        'error': None,
        'trace_id': trace_id,
        'max_messages': req.max_messages,
        'actor_role': actor['role'],
        'actor_id': actor['id'],
    }
    await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
    await r.rpush('copilot:queue:suggest', task_id)
    await r.publish(_stream_chan(task_id), json.dumps({'event': 'status', 'data': meta}, ensure_ascii=False))

    return SuggestCreated(task_id=task_id)


@router.get('/suggest/{task_id}', response_model=SuggestStatusOut)
async def suggest_status(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        raise HTTPException(status_code=404, detail='task not found')
    meta = require_task_access(actor, json.loads(raw))

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

    if out['result'] is not None:
        out['result'] = DraftV1.model_validate(out['result'])

    return SuggestStatusOut.model_validate(out)


@router.post('/suggest/{task_id}/cancel')
async def cancel(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        raise HTTPException(status_code=404, detail='task not found')
    require_task_access(actor, json.loads(raw))
    await r.set(_task_cancel_key(task_id), '1', ex=3600)
    return {'ok': True}


@router.get('/suggest/{task_id}/stream')
async def suggest_stream(task_id: str, actor=Depends(require_operator)):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        raise HTTPException(status_code=404, detail='task not found')
    meta = require_task_access(actor, json.loads(raw))

    pubsub = r.pubsub()
    await pubsub.subscribe(_stream_chan(task_id))

    async def gen():
        try:
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
async def state(
    conversation_id: str,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    await require_conversation_access(db, actor, conversation_id)

    r = get_redis()
    raw = await r.get(_state_key(conversation_id))
    if not raw:
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
    await require_conversation_access(db, actor, req.conversation_id)

    r = get_redis()
    trace_id = _trace(x_request_id)

    state_raw = await r.get(_state_key(req.conversation_id))
    if not state_raw:
        raise HTTPException(status_code=409, detail='no copilot state for conversation')

    st = json.loads(state_raw)
    state_before = st
    tool_prompt_hash = make_prompt_hash(
        {
            'conversation_id': req.conversation_id,
            'tool': req.tool.value,
            'intent': st.get('intent'),
            'phase': st.get('phase'),
            'params': req.params,
            'idempotency_key': req.idempotency_key,
        }
    )

    intent = st['intent']
    phase = st['phase']
    plan = st['plan']

    missing_fields: list[str] = []
    if st.get('last_analyze') and isinstance(st['last_analyze'], dict):
        missing_fields = st['last_analyze'].get('missing_fields') or []

    tools_ui = resolve_tools(
        Intent(intent),
        Phase(phase),
        missing_fields=missing_fields,
        execution_params=req.params,
    )
    dynamic_allow = {tool.tool.value: tool for tool in tools_ui}

    ui = dynamic_allow.get(req.tool.value)
    if ui is None:
        raise HTTPException(status_code=403, detail='tool not allowed by policy-pack')
    if not ui.enabled:
        raise HTTPException(status_code=409, detail=f'tool disabled: {ui.reason}')

    await add_audit(
        db,
        trace_id=trace_id,
        actor_role=actor['role'],
        actor_id=actor['id'],
        conversation_id=req.conversation_id,
        event_type='tool_called',
        payload={'tool': req.tool.value, 'idempotency_key': req.idempotency_key, 'params': req.params},
        state_before=state_before,
        prompt_hash=tool_prompt_hash,
        policy_version=POLICY_VERSION,
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
            headers=build_internal_headers(
                actor_role=actor['role'],
                actor_id=actor['id'],
                request_id=trace_id,
                issuer='backend',
            ),
            json=payload,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f'mcp-tools error: {resp.text}')

    tool_resp = ToolExecuteResponse.model_validate(resp.json())

    current_plan = Plan.model_validate(plan)
    deterministic_plan = reduce_plan_after_tool(current_plan, req.tool.value)

    exp = await llm_explain(req.tool.value, tool_resp.result, deterministic_plan)

    if req.tool.value in {'create_case', 'block_card'}:
        exp = stub_explain(req.tool.value, tool_resp.result, deterministic_plan)

    mod = moderate_output(exp.ghost_text)
    if not mod['ok']:
        exp = exp.model_copy(
            update={
                'ghost_text': 'Действие выполнено. Для безопасности используйте только подтвержденный результат инструмента и не запрашивайте коды из SMS/Push или полный номер карты.'
            }
        )

    new_phase = phase_from_plan(deterministic_plan)
    exp = exp.model_copy(
        update={
            'updates': exp.updates.model_copy(
                update={
                    'phase': new_phase,
                    'plan': deterministic_plan,
                }
            )
        }
    )

    new_state = {
        'conversation_id': req.conversation_id,
        'intent': intent,
        'phase': new_phase.value,
        'plan': deterministic_plan.model_dump(),
        'last_analyze': st.get('last_analyze'),
        'last_draft': st.get('last_draft'),
    }
    await r.set(_state_key(req.conversation_id), json.dumps(new_state, ensure_ascii=False), ex=86400)

    case_id = tool_resp.result.get('case_id')
    if case_id is None:
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
        payload_data = {'tool': req.tool.value, 'result': tool_resp.result}
        tl = CaseTimeline(
            case_id=case_id,
            kind='tool_result',
            payload=json.dumps(payload_data, ensure_ascii=False),
            payload_json=payload_data,
        )
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
        payload={
            'tool': req.tool.value,
            'result': tool_resp.result,
            'llm_updates_ignored': True,
        },
        state_before=state_before,
        state_after=new_state,
        prompt_hash=tool_prompt_hash,
        policy_version=POLICY_VERSION,
        cache_info={'moderation_blocked': not mod['ok']},
    )

    return ExecuteToolResponse(tool=req.tool, result=tool_resp.result, explain=exp)


@router.post('/profile/confirm', response_model=ProfileConfirmResponse)
async def profile_confirm(
    req: ProfileConfirmRequest,
    actor=Depends(require_operator),
    db: AsyncSession = Depends(get_db),
    x_request_id: str | None = Header(default=None, alias='X-Request-Id'),
):
    await require_conversation_access(db, actor, req.conversation_id)
    trace_id = req.trace_id or _trace(x_request_id)

    case_id = req.case_id
    c = None
    if case_id is None:
        c = (
            await db.execute(
                select(Case)
                .where(Case.conversation_id == req.conversation_id)
                .order_by(Case.created_at.desc())
            )
        ).scalars().first()
        if c:
            case_id = c.id
    else:
        c = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()

    if case_id is None or c is None:
        return ProfileConfirmResponse(stored=0)

    stored = 0
    confirmed = json.loads(c.facts_confirmed_json or '[]')
    pending = json.loads(c.facts_pending_json or '[]')

    for f in req.fields:
        row = CaseProfileField(
            case_id=case_id,
            field_name=f.field_name,
            value=f.value,
            trace_id=trace_id,
            confirmed_by=actor['id'],
        )
        db.add(row)
        stored += 1

        if f.field_name not in confirmed:
            confirmed.append(f.field_name)
        pending = [item for item in pending if item != f.field_name]

    c.facts_confirmed_json = json.dumps(confirmed, ensure_ascii=False)
    c.facts_pending_json = json.dumps(pending, ensure_ascii=False)
    c.updated_at = datetime.now(timezone.utc)
    db.add(c)

    if stored:
        timeline_payload = {'stored': stored}
        tl = CaseTimeline(
            case_id=case_id,
            kind='profile_confirmed',
            payload=json.dumps(timeline_payload, ensure_ascii=False),
            payload_json=timeline_payload,
        )
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
        prompt_hash=make_prompt_hash(req.conversation_id, trace_id, [x.model_dump() for x in req.fields]),
        policy_version=POLICY_VERSION,
    )

    return ProfileConfirmResponse(stored=stored)