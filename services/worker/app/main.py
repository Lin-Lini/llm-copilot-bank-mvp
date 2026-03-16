from __future__ import annotations

import asyncio
import hashlib
import json
import os
import traceback
from datetime import datetime, timezone

from sqlalchemy import select

from shared.config import settings
from shared.db import SessionLocal, init_db
from shared.kafka_bus import kafka_bus
# Use asynchronous LLM client instead of synchronous stub.  The client
# falls back to the stub when no external URLs are configured.
from shared.llm_client import analyze as llm_analyze, draft as llm_draft, stream_ghost
from shared.moderator import moderate_input, moderate_output
from shared.pii import redact
from shared.policy import build_plan, allowed_tools
from shared.plan_utils import reduce_plan_after_analyze
from shared.redis_client import get_redis
from shared.models import Message, AuditEvent
from shared.rag_search import hybrid_search
from contracts.schemas import Intent, Phase, ToolName


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_key(task_id: str) -> str:
    return f'copilot:task:{task_id}'


def _task_result_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:result'


def _task_cancel_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:cancel'


def _state_key(conversation_id: str) -> str:
    return f'copilot:state:{conversation_id}'


def _stream_chan(task_id: str) -> str:
    return f'copilot:stream:{task_id}'


def _analyze_cache_key(conversation_id: str, last_message_id: int) -> str:
    return f'copilot:cache:analyze:{conversation_id}:{last_message_id}'


def _draft_cache_key(conversation_id: str, last_message_id: int) -> str:
    return f'copilot:cache:draft:{conversation_id}:{last_message_id}'


def _rag_cache_key(redacted_history: str) -> str:
    h = hashlib.sha256(redacted_history.encode('utf-8')).hexdigest()
    return f'copilot:cache:rag:{h}'


async def audit(db, *, trace_id: str, actor_role: str, actor_id: str, event_type: str, payload: dict, conversation_id: str | None = None, case_id: str | None = None):
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


async def rag_search(db, query: str, top_k: int = 5) -> list[dict]:
    return await hybrid_search(db, query, top_k=top_k)


async def publish(r, task_id: str, event: str, data):
    await r.publish(_stream_chan(task_id), json.dumps({'event': event, 'data': data}, ensure_ascii=False))


async def run_task(task_id: str):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        return
    meta = json.loads(raw)

    # cancel early
    if await r.get(_task_cancel_key(task_id)):
        meta['status'] = 'canceled'
        meta['updated_at'] = _now()
        await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
        await publish(r, task_id, 'status', meta)
        return

    conv_id = meta['conversation_id']
    trace_id = meta['trace_id']
    actor_id = meta.get('actor_id') or 'op'

    meta['status'] = 'running'
    meta['updated_at'] = _now()
    await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
    await publish(r, task_id, 'status', meta)

    async with SessionLocal() as db:
        try:
            msgs = (await db.execute(
                select(Message)
                .where(Message.conversation_id == conv_id)
                .order_by(Message.id.desc())
                .limit(int(meta.get('max_messages') or 20))
            )).scalars().all()
            msgs = list(reversed(msgs))
            last_id = msgs[-1].id if msgs else 0

            history = '\n'.join([f"{m.actor_role}: {m.content}" for m in msgs])

            await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='suggest_created', payload={'task_id': task_id, 'last_message_id': last_id})
            await kafka_bus.publish('copilot.suggest.v1', {'event': 'suggest_created', 'task_id': task_id, 'conversation_id': conv_id, 'trace_id': trace_id})

            await publish(r, task_id, 'progress', {'step': 'moderate_input', 'pct': 0.1})
            mod = moderate_input(history)
            await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='moderation_input', payload=mod)

            redacted, pii_sum = redact(history)
            await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='pii_redaction', payload={'summary': pii_sum})

            if await r.get(_task_cancel_key(task_id)):
                raise RuntimeError('canceled')

            # ANALYZE with possible cache and incremental update
            akey = _analyze_cache_key(conv_id, last_id)
            cached_a = await r.get(akey)
            if cached_a:
                # load previous analyze result for incremental update
                prev_an = json.loads(cached_a)
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_hit', payload={'kind': 'analyze'})
                # call analyze with previous result so the LLM can perform incremental analysis
                an = await llm_analyze(redacted, prev_result=prev_an)
            else:
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_miss', payload={'kind': 'analyze'})
                an = await llm_analyze(redacted)
            an_obj = an.model_dump()
            # store updated analyze result in cache
            await r.set(akey, json.dumps(an_obj, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'analyze', 'pct': 0.35})

            # RAG cache
            rkey = _rag_cache_key(redacted)
            cached_sources = await r.get(rkey)
            if cached_sources:
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_hit', payload={'kind': 'rag'})
                sources = json.loads(cached_sources)
            else:
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_miss', payload={'kind': 'rag'})
                # RAG query: use the last non-operator message + concise analyze summary
                last_customer = ''
                for m in reversed(msgs):
                    if (m.actor_role or '').lower() != 'operator':
                        last_customer = m.content
                        break
                rag_q = (an_obj.get('summary_public') or '').strip()
                if last_customer:
                    rag_q = (rag_q + '\n' + last_customer).strip() if rag_q else last_customer
                else:
                    rag_q = rag_q or redacted
                sources = await rag_search(db, rag_q, top_k=5)
                await r.set(rkey, json.dumps(sources, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'rag', 'pct': 0.55})

            # policy pack
            intent = an_obj['intent']
            phase = an_obj['phase']
            plan = build_plan(Intent(intent))
            # update plan deterministically based on analyze phase
            try:
                # validate analyze object to pass to reducer
                from contracts.schemas import AnalyzeV1
                an_model = AnalyzeV1.model_validate(an_obj)
                plan = reduce_plan_after_analyze(plan, an_model)
            except Exception:
                # on validation failure fallback to original plan
                pass
            tools_ui = allowed_tools(Intent(intent), Phase(phase))
            # apply dynamic enablement based on missing_fields from analyze
            missing_fields = an_obj.get('missing_fields') or []
            patched_tools = []
            for _t in tools_ui:
                t = _t
                # disable get_transactions until card possession, amount and datetime are confirmed
                if t.tool == ToolName.get_transactions and any(
                    mf in ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'] for mf in missing_fields
                ):
                    t = t.model_copy(update={
                        'enabled': False,
                        'reason': 'Нужно уточнить наличие карты, сумму и время операции.',
                    })
                # disable block_card until customer confirmation is collected
                if t.tool == ToolName.block_card and 'customer_confirm_block' in missing_fields:
                    t = t.model_copy(update={
                        'enabled': False,
                        'reason': 'Нужно подтверждение клиента.',
                    })
                patched_tools.append(t)
            tools_ui = patched_tools


            # DRAFT cache
            dkey = _draft_cache_key(conv_id, last_id)
            cached_d = await r.get(dkey)
            if cached_d:
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_hit', payload={'kind': 'draft'})
                d_obj = json.loads(cached_d)
            else:
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='cache_miss', payload={'kind': 'draft'})
                from contracts.schemas import AnalyzeV1, SourceOut
                an_model = AnalyzeV1.model_validate(an_obj)
                src_models = [SourceOut.model_validate(s) for s in sources]
                # call asynchronous draft
                d = await llm_draft(an_model, plan, tools_ui, src_models, history=redacted)

                # output moderation
                mout = moderate_output(d.ghost_text)
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='moderation_output', payload=mout)
                if not mout['ok']:
                    d = d.model_copy(update={'ghost_text': 'Понял. Чтобы корректно продолжить, уточните, пожалуйста, базовые детали (карта у вас, сумма/время операции). Мы не запрашиваем коды из SMS/Push и данные карты.'})

                d_obj = d.model_dump()
                await r.set(dkey, json.dumps(d_obj, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'draft', 'pct': 0.75})

            # stream ghost_text: if a streaming endpoint is configured use it; otherwise fall back to slicing the ghost text
            try:
                # Attempt to stream from external LLM if configured
                from contracts.schemas import AnalyzeV1
                an_model = AnalyzeV1.model_validate(an_obj)
                full_ghost = ''
                async for delta in stream_ghost(an_model, plan, tools_ui, history=redacted):
                    # check for cancel mid-stream
                    if await r.get(_task_cancel_key(task_id)):
                        raise RuntimeError('canceled')
                    full_ghost += delta
                    await publish(r, task_id, 'ghost_text', {'delta': delta, 'full': full_ghost})
                # when streaming completes, ensure final ghost_text matches full_ghost
                if full_ghost:
                    d_obj['ghost_text'] = full_ghost
            except Exception:
                # Fallback: slice the ghost_text from d_obj
                ghost = d_obj.get('ghost_text', '') or ''
                buf = ''
                for i in range(0, len(ghost), 40):
                    if await r.get(_task_cancel_key(task_id)):
                        raise RuntimeError('canceled')
                    chunk = ghost[i:i + 40]
                    buf += chunk
                    await publish(r, task_id, 'ghost_text', {'delta': chunk, 'full': buf})
                    await asyncio.sleep(0.05)

            # state store
            state = {
                'conversation_id': conv_id,
                'intent': an_obj['intent'],
                'phase': an_obj['phase'],
                'plan': plan.model_dump(),
                'last_analyze': an_obj,
                'last_draft': d_obj,
            }
            await r.set(_state_key(conv_id), json.dumps(state, ensure_ascii=False))

            await publish(r, task_id, 'result', d_obj)

            meta['status'] = 'succeeded'
            meta['updated_at'] = _now()
            await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
            await r.set(_task_result_key(task_id), json.dumps(d_obj, ensure_ascii=False), ex=3600)
            await publish(r, task_id, 'status', meta)

            await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='suggest_ready', payload={'task_id': task_id, 'sources_count': len(sources)})
            await kafka_bus.publish('copilot.suggest.v1', {'event': 'suggest_ready', 'task_id': task_id, 'conversation_id': conv_id, 'trace_id': trace_id})

        except RuntimeError as e:
            if str(e) == 'canceled':
                meta['status'] = 'canceled'
                meta['updated_at'] = _now()
                await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
                await publish(r, task_id, 'status', meta)
                await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='suggest_canceled', payload={'task_id': task_id})
                return
            raise
        except Exception:
            err = traceback.format_exc(limit=6)
            meta['status'] = 'failed'
            meta['updated_at'] = _now()
            meta['error'] = err
            await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
            await publish(r, task_id, 'status', meta)
            await audit(db, trace_id=trace_id, actor_role='operator', actor_id=actor_id, conversation_id=conv_id, event_type='suggest_failed', payload={'task_id': task_id, 'error': err})


async def main():
    await init_db()
    await kafka_bus.start()
    r = get_redis()

    while True:
        item = await r.blpop('copilot:queue:suggest', timeout=5)
        if not item:
            continue
        _, task_id = item
        await run_task(task_id)


if __name__ == '__main__':
    asyncio.run(main())
