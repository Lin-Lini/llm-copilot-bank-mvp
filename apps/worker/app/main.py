from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from datetime import datetime, timezone

from sqlalchemy import select

from contracts.schemas import AnalyzeV1, DraftV1, Intent, Phase, RiskLevel, ToolName
from libs.common import llm_stub
from libs.common.config import settings
from libs.common.db import SessionLocal, init_db
from libs.common.kafka_bus import kafka_bus
from libs.common.llm_client import analyze as llm_analyze, draft as llm_draft, stream_ghost
from libs.common.models import AuditEvent, Message
from libs.common.moderator import moderate_input, moderate_output, moderation_mode
from libs.common.pii import redact
from libs.common.plan_utils import reduce_plan_after_analyze
from libs.common.policy import allowed_tools, build_plan
from libs.common.rag_search import hybrid_search
from libs.common.redis_client import get_redis


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


async def audit(
    db,
    *,
    trace_id: str,
    actor_role: str,
    actor_id: str,
    event_type: str,
    payload: dict,
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

    await kafka_bus.publish(
        'copilot.audit.v1',
        {
            'trace_id': trace_id,
            'actor_role': actor_role,
            'actor_id': actor_id,
            'conversation_id': conversation_id,
            'case_id': case_id,
            'event_type': event_type,
            'payload': payload,
        },
    )


async def rag_search(db, query: str, top_k: int = 5) -> list[dict]:
    return await hybrid_search(db, query, top_k=top_k)


async def publish(r, task_id: str, event: str, data):
    await r.publish(_stream_chan(task_id), json.dumps({'event': event, 'data': data}, ensure_ascii=False))


def _safe_draft(flags: list[dict], plan) -> DraftV1:
    flag_text = ', '.join(flag.get('type', 'unknown') for flag in flags) or 'safety'
    an = AnalyzeV1(
        schema_version='1.0',
        intent=Intent.SuspiciousTransaction,
        phase=Phase.Collect,
        confidence=0.95,
        summary_public='Обнаружены признаки рискованного/сомнительного сценария. Нужен безопасный режим ответа.',
        risk_level=RiskLevel.high,
        facts=llm_stub.analyze('мошенническая операция').facts,
        profile_update=llm_stub.analyze('мошенническая операция').profile_update,
        missing_fields=['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'],
        next_questions=[
            'Подтвердите, пожалуйста, карта сейчас у вас?',
            'Подтвердите сумму и примерное время спорной операции.',
        ],
        tools_suggested=[],
        danger_flags=llm_stub.analyze('мошенническая операция').danger_flags,
        risk_checklist=llm_stub.analyze('мошенническая операция').risk_checklist,
        analytics_tags=['safe_mode', flag_text],
    )
    tools_ui = allowed_tools(Intent.SuspiciousTransaction, Phase.Collect)
    d = llm_stub.draft(an, plan, tools_ui, [])
    return d.model_copy(
        update={
            'ghost_text': 'Нужен безопасный режим. Не запрашивайте коды из SMS/Push, ПИН, CVV/CVC и не предлагайте устанавливать приложения удаленного доступа. Сначала уточните только безопасные детали операции: карта у клиента, сумма и время.',
        }
    )

def _stabilize_draft_ghost(an_obj: dict, tools_ui: list, d_obj: dict) -> dict:
    text = (d_obj.get('ghost_text') or '').strip()
    if not text:
        return d_obj

    phase = an_obj.get('phase')
    if phase == Phase.Explain.value:
        return d_obj

    lower = text.lower()

    premature_block_claim = any(
        x in lower
        for x in [
            'карта заблокирована',
            'ваша карта заблокирована',
            'мы заблокировали карту',
            'я заблокировала карту',
            'я заблокирую вашу карту',
            'операция выполнена',
        ]
    )

    block_tool = next((t for t in tools_ui if t.tool == ToolName.block_card), None)
    if not premature_block_claim or block_tool is None:
        return d_obj

    if block_tool.enabled:
        safe_text = (
            'Подтверждение на блокировку получено. Сейчас выполняю блокировку карты. '
            'После подтверждения результата сообщу вам статус и дальнейшие шаги.'
        )
    else:
        safe_text = (
            'Чтобы безопасно заблокировать карту, сначала подтвержу действие и уточню '
            'только безопасные данные для идентификации. После выполнения сообщу результат.'
        )

    d_obj['ghost_text'] = safe_text
    return d_obj

async def _run_analyze(redacted: str, *, safe_mode: str, cached_a: str | None):
    if cached_a:
        return json.loads(cached_a), True

    if safe_mode != 'ok':
        an = llm_stub.analyze(redacted)
    else:
        an = await llm_analyze(redacted)
    return an.model_dump(), False


async def _run_draft(
    *,
    redacted: str,
    safe_mode: str,
    an_obj: dict,
    plan,
    tools_ui,
    sources: list[dict],
    cached_d: str | None,
):
    if cached_d:
        return json.loads(cached_d), True

    an_model = AnalyzeV1.model_validate(an_obj)
    if safe_mode == 'block':
        d = _safe_draft([], plan)
    elif safe_mode == 'warn':
        src_models = []
        d = llm_stub.draft(an_model, plan, tools_ui, src_models)
        d = d.model_copy(
            update={
                'ghost_text': 'Нужен безопасный режим. Не запрашивайте коды из SMS/Push, ПИН, CVV/CVC и не предлагайте удаленный доступ. Уточните только безопасные детали операции и затем переходите к следующему действию.'
            }
        )
    else:
        from contracts.schemas import SourceOut

        src_models = [SourceOut.model_validate(s) for s in sources]
        d = await llm_draft(an_model, plan, tools_ui, src_models, history=redacted)

    mout = moderate_output(d.ghost_text)
    return d.model_dump(), False, mout


async def run_task(task_id: str):
    r = get_redis()
    raw = await r.get(_task_key(task_id))
    if not raw:
        return
    meta = json.loads(raw)

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
            msgs = (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.id.desc())
                    .limit(int(meta.get('max_messages') or 20))
                )
            ).scalars().all()
            msgs = list(reversed(msgs))
            last_id = msgs[-1].id if msgs else 0
            history = '\n'.join([f"{m.actor_role}: {m.content}" for m in msgs])

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_created',
                payload={'task_id': task_id, 'last_message_id': last_id},
            )
            await kafka_bus.publish(
                'copilot.suggest.v1',
                {'event': 'suggest_created', 'task_id': task_id, 'conversation_id': conv_id, 'trace_id': trace_id},
            )

            await publish(r, task_id, 'progress', {'step': 'moderate_input', 'pct': 0.1})
            mod = moderate_input(history)
            safe_mode = moderation_mode(mod)
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='moderation_input',
                payload={**mod, 'mode': safe_mode},
            )

            redacted, pii_sum = redact(history)
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='pii_redaction',
                payload={'summary': pii_sum},
            )

            if safe_mode != 'ok':
                redacted = '\n'.join(redacted.splitlines()[-6:])[:1500]
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='safe_mode_applied',
                    payload={'mode': safe_mode, 'history_trimmed': True},
                )

            if await r.get(_task_cancel_key(task_id)):
                raise RuntimeError('canceled')

            akey = _analyze_cache_key(conv_id, last_id)
            cached_a = await r.get(akey)
            an_obj, analyze_cached = await _run_analyze(redacted, safe_mode=safe_mode, cached_a=cached_a)
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='cache_hit' if analyze_cached else 'cache_miss',
                payload={'kind': 'analyze'},
            )
            if not analyze_cached:
                await r.set(akey, json.dumps(an_obj, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'analyze', 'pct': 0.35})

            sources: list[dict] = []
            if safe_mode == 'ok':
                rkey = _rag_cache_key(redacted)
                cached_sources = await r.get(rkey)
                if cached_sources:
                    await audit(
                        db,
                        trace_id=trace_id,
                        actor_role='operator',
                        actor_id=actor_id,
                        conversation_id=conv_id,
                        event_type='cache_hit',
                        payload={'kind': 'rag'},
                    )
                    sources = json.loads(cached_sources)
                else:
                    await audit(
                        db,
                        trace_id=trace_id,
                        actor_role='operator',
                        actor_id=actor_id,
                        conversation_id=conv_id,
                        event_type='cache_miss',
                        payload={'kind': 'rag'},
                    )
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
            else:
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='rag_skipped',
                    payload={'mode': safe_mode},
                )

            await publish(r, task_id, 'progress', {'step': 'rag', 'pct': 0.55})

            intent = an_obj['intent']
            phase = an_obj['phase']
            plan = build_plan(Intent(intent))
            try:
                an_model = AnalyzeV1.model_validate(an_obj)
                plan = reduce_plan_after_analyze(plan, an_model)
            except Exception:
                pass

            tools_ui = allowed_tools(Intent(intent), Phase(phase))
            missing_fields = an_obj.get('missing_fields') or []
            patched_tools = []
            for _t in tools_ui:
                t = _t

                if t.tool == ToolName.get_transactions and any(
                    mf in ['card_in_possession', 'txn_amount_confirm', 'txn_datetime_confirm'] for mf in missing_fields
                ):
                    t = t.model_copy(update={'enabled': False, 'reason': 'Нужно уточнить наличие карты, сумму и время операции.'})

                if t.tool == ToolName.block_card:
                    confirmed_from_state = 'customer_confirm_block' not in missing_fields
                    high_risk_intent = Intent(intent) in {Intent.BlockCard, Intent.LostStolen}

                    if confirmed_from_state or high_risk_intent:
                        reason = 'Подтверждение клиента получено.'
                        if high_risk_intent and not confirmed_from_state:
                            reason = 'Сценарий повышенного риска допускает блокировку.'
                        t = t.model_copy(update={'enabled': True, 'reason': reason})
                    else:
                        t = t.model_copy(update={'enabled': False, 'reason': 'Нужно подтверждение клиента.'})

                if safe_mode != 'ok' and t.tool != ToolName.create_case:
                    t = t.model_copy(update={'enabled': False, 'reason': 'В safe mode доступны только безопасные действия.'})

                patched_tools.append(t)

            tools_ui = patched_tools

            dkey = _draft_cache_key(conv_id, last_id)
            cached_d = await r.get(dkey)
            if cached_d:
                d_obj = json.loads(cached_d)
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='cache_hit',
                    payload={'kind': 'draft'},
                )
            else:
                result = await _run_draft(
                    redacted=redacted,
                    safe_mode=safe_mode,
                    an_obj=an_obj,
                    plan=plan,
                    tools_ui=tools_ui,
                    sources=sources,
                    cached_d=None,
                )
                d_obj, _, mout = result
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='cache_miss',
                    payload={'kind': 'draft'},
                )
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='moderation_output',
                    payload=mout,
                )
                if not mout['ok']:
                    d_obj['ghost_text'] = 'Понял. Уточните только безопасные детали операции. Мы не запрашиваем коды из SMS/Push, ПИН, CVV/CVC и не предлагаем удаленный доступ.'
                await r.set(dkey, json.dumps(d_obj, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'draft', 'pct': 0.75})

            streamed = False
            if safe_mode == 'ok':
                try:
                    an_model = AnalyzeV1.model_validate(an_obj)
                    full_ghost = ''
                    async for delta in stream_ghost(an_model, plan, tools_ui, history=redacted):
                        if await r.get(_task_cancel_key(task_id)):
                            raise RuntimeError('canceled')
                        full_ghost += delta

                    if full_ghost:
                        d_obj['ghost_text'] = full_ghost
                        d_obj = _stabilize_draft_ghost(an_obj, tools_ui, d_obj)

                        safe_full = d_obj.get('ghost_text', '') or ''
                        buf = ''
                        for i in range(0, len(safe_full), 40):
                            if await r.get(_task_cancel_key(task_id)):
                                raise RuntimeError('canceled')
                            chunk = safe_full[i:i + 40]
                            buf += chunk
                            await publish(r, task_id, 'ghost_text', {'delta': chunk, 'full': buf})

                    streamed = True
                except Exception:
                    streamed = False

            d_obj = _stabilize_draft_ghost(an_obj, tools_ui, d_obj)

            if not streamed:
                ghost = d_obj.get('ghost_text', '') or ''
                buf = ''
                for i in range(0, len(ghost), 40):
                    if await r.get(_task_cancel_key(task_id)):
                        raise RuntimeError('canceled')
                    chunk = ghost[i:i + 40]
                    buf += chunk
                    await publish(r, task_id, 'ghost_text', {'delta': chunk, 'full': buf})
                    await asyncio.sleep(0.05)

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

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_ready',
                payload={'task_id': task_id, 'sources_count': len(sources), 'mode': safe_mode},
            )
            await kafka_bus.publish(
                'copilot.suggest.v1',
                {'event': 'suggest_ready', 'task_id': task_id, 'conversation_id': conv_id, 'trace_id': trace_id},
            )

        except RuntimeError as e:
            if str(e) == 'canceled':
                meta['status'] = 'canceled'
                meta['updated_at'] = _now()
                await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
                await publish(r, task_id, 'status', meta)
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='suggest_canceled',
                    payload={'task_id': task_id},
                )
                return
            raise
        except Exception:
            err = traceback.format_exc(limit=6)
            meta['status'] = 'failed'
            meta['updated_at'] = _now()
            meta['error'] = err
            await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))
            await publish(r, task_id, 'status', meta)
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_failed',
                payload={'task_id': task_id, 'error': err},
            )


async def main():
    await init_db()

    queue_name = 'copilot:queue:suggest'
    r = get_redis()

    while True:
        item = await r.blpop(queue_name, timeout=1)
        if not item:
            await asyncio.sleep(0.1)
            continue
        _, task_id = item
        if isinstance(task_id, bytes):
            task_id = task_id.decode('utf-8', errors='ignore')
        await run_task(str(task_id))


if __name__ == '__main__':
    asyncio.run(main())
