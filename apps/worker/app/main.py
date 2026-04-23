from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import time
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from contracts.schemas import (
    AnalyzeV1,
    CardState,
    ChannelHint,
    CompromiseSignal,
    DangerFlag,
    DisputeSubtype,
    DraftV1,
    Intent,
    Phase,
    RequestedAction,
    RiskChecklistItem,
    RiskLevel,
    SourceOut,
    StatusContext,
)
from libs.common.analyze_guardrails import normalize_analyze
from libs.common import llm_stub
from libs.common.audit_store import add_audit_event
from libs.common.config import settings
from libs.common.copilot_postprocess import repair_draft
from libs.common.db import SessionLocal, init_db
from libs.common.kafka_bus import kafka_bus
from libs.common.llm_client import analyze as llm_analyze, draft as llm_draft, stream_ghost
from libs.common.models import Message
from libs.common.moderator import (
    moderate_model_output,
    moderate_retrieved_chunks,
    moderate_user_input,
    summarize_security_moderation,
)
from libs.common.pii import redact
from libs.common.policy_meta import POLICY_VERSION, make_prompt_hash
from libs.common.rag_search import hybrid_search
from libs.common.redis_client import get_redis
from libs.common.state_engine import build_plan, phase_from_plan, reduce_plan_after_analyze, resolve_tools


_TERMINAL_TASK_STATUSES = {'succeeded', 'failed', 'canceled'}
_RUNNING_INDEX_KEY = 'copilot:tasks:running'
_QUEUE_NAME = 'copilot:queue:suggest'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch() -> int:
    return int(time.time())


def _worker_id() -> str:
    return f'{socket.gethostname()}:{os.getpid()}'


def _task_key(task_id: str) -> str:
    return f'copilot:task:{task_id}'


def _task_result_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:result'


def _task_cancel_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:cancel'


def _task_lease_key(task_id: str) -> str:
    return f'copilot:task:{task_id}:lease'


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


def _lease_deadline_ts() -> int:
    return _epoch() + int(settings.worker_lease_ttl_sec)


async def _load_meta(r, task_id: str) -> dict | None:
    raw = await r.get(_task_key(task_id))
    if not raw:
        return None
    return json.loads(raw)


async def _store_meta(r, task_id: str, meta: dict) -> None:
    await r.set(_task_key(task_id), json.dumps(meta, ensure_ascii=False))


async def _publish_status(r, task_id: str, meta: dict) -> None:
    await publish(r, task_id, 'status', meta)


async def _set_status(
    r,
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    extra: dict | None = None,
) -> dict | None:
    meta = await _load_meta(r, task_id)
    if not meta:
        return None
    meta['status'] = status
    meta['updated_at'] = _now()
    if error is not None:
        meta['error'] = error
    if extra:
        meta.update(extra)
    await _store_meta(r, task_id, meta)
    await _publish_status(r, task_id, meta)
    return meta


async def _is_canceled(r, task_id: str) -> bool:
    return bool(await r.get(_task_cancel_key(task_id)))


async def _claim_task(r, task_id: str, worker_id: str) -> dict | None:
    meta = await _load_meta(r, task_id)
    if not meta:
        return None
    if meta.get('status') in _TERMINAL_TASK_STATUSES:
        return None

    lease_key = _task_lease_key(task_id)
    owner = await r.get(lease_key)
    if owner and owner != worker_id:
        return None
    if not owner:
        ok = await r.set(lease_key, worker_id, ex=int(settings.worker_lease_ttl_sec), nx=True)
        if not ok:
            return None
    else:
        await r.expire(lease_key, int(settings.worker_lease_ttl_sec))

    deadline = _lease_deadline_ts()
    meta.update(
        {
            'status': 'running',
            'updated_at': _now(),
            'lease_owner': worker_id,
            'lease_expires_at': deadline,
            'heartbeat_at': _now(),
        }
    )
    await _store_meta(r, task_id, meta)
    await r.zadd(_RUNNING_INDEX_KEY, {task_id: deadline})
    return meta


async def _release_task(r, task_id: str, worker_id: str) -> None:
    lease_key = _task_lease_key(task_id)
    owner = await r.get(lease_key)
    if owner == worker_id:
        await r.delete(lease_key)
    await r.zrem(_RUNNING_INDEX_KEY, task_id)


async def _heartbeat_loop(r, task_id: str, worker_id: str, stop_event: asyncio.Event) -> None:
    interval = max(1, int(settings.worker_heartbeat_interval_sec))
    ttl = max(interval + 1, int(settings.worker_lease_ttl_sec))

    while not stop_event.is_set():
        await asyncio.sleep(interval)
        if stop_event.is_set():
            return

        owner = await r.get(_task_lease_key(task_id))
        if owner != worker_id:
            return

        await r.expire(_task_lease_key(task_id), ttl)
        meta = await _load_meta(r, task_id)
        if not meta or meta.get('status') != 'running':
            return

        deadline = _epoch() + ttl
        meta['heartbeat_at'] = _now()
        meta['updated_at'] = _now()
        meta['lease_owner'] = worker_id
        meta['lease_expires_at'] = deadline
        await _store_meta(r, task_id, meta)
        await r.zadd(_RUNNING_INDEX_KEY, {task_id: deadline})


async def _reclaim_expired_tasks(r) -> None:
    now = _epoch()
    expired = await r.zrangebyscore(
        _RUNNING_INDEX_KEY,
        min=0,
        max=now,
        start=0,
        num=int(settings.worker_reclaim_batch),
    )

    for task_id in expired:
        if not task_id:
            continue

        owner = await r.get(_task_lease_key(task_id))
        if owner:
            await r.zadd(_RUNNING_INDEX_KEY, {task_id: _lease_deadline_ts()})
            continue

        meta = await _load_meta(r, task_id)
        if not meta:
            await r.zrem(_RUNNING_INDEX_KEY, task_id)
            continue

        if meta.get('status') != 'running':
            await r.zrem(_RUNNING_INDEX_KEY, task_id)
            continue

        if await _is_canceled(r, task_id):
            meta['status'] = 'canceled'
            meta['updated_at'] = _now()
            meta['reclaimed_at'] = _now()
            await _store_meta(r, task_id, meta)
            await r.zrem(_RUNNING_INDEX_KEY, task_id)
            continue

        meta['status'] = 'queued'
        meta['updated_at'] = _now()
        meta['error'] = None
        meta['reclaimed_at'] = _now()
        meta['requeue_count'] = int(meta.get('requeue_count') or 0) + 1
        meta.pop('lease_owner', None)
        meta.pop('lease_expires_at', None)
        await _store_meta(r, task_id, meta)
        await r.zrem(_RUNNING_INDEX_KEY, task_id)
        await r.rpush(_QUEUE_NAME, task_id)


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
    retrieval_snapshot: list[dict] | None = None,
    state_before: dict | None = None,
    state_after: dict | None = None,
    prompt_hash: str | None = None,
    cache_info: dict | None = None,
):
    await add_audit_event(
        db,
        trace_id=trace_id,
        actor_role=actor_role,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload,
        conversation_id=conversation_id,
        case_id=case_id,
        retrieval_snapshot=retrieval_snapshot,
        state_before=state_before,
        state_after=state_after,
        prompt_hash=prompt_hash,
        policy_version=POLICY_VERSION,
        cache_info=cache_info,
    )


async def rag_search(db, query: str, top_k: int = 5) -> list[dict]:
    return await hybrid_search(db, query, top_k=top_k)


async def publish(r, task_id: str, event: str, data) -> None:
    await r.publish(_stream_chan(task_id), json.dumps({'event': event, 'data': data}, ensure_ascii=False))


def _safe_draft(flags: list[Any], plan) -> DraftV1:
    normalized_flags: list[str] = []
    for flag in flags:
        if isinstance(flag, dict):
            normalized_flags.append(str(flag.get('type') or flag.get('flag') or 'unknown'))
        else:
            normalized_flags.append(str(flag))
    flag_text = ', '.join(normalized_flags) or 'safety'

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
    tools_ui = resolve_tools(Intent.SuspiciousTransaction, Phase.Collect, analyze=an)
    d = llm_stub.draft(an, plan, tools_ui, [])
    return d.model_copy(
        update={
            'ghost_text': 'Нужен безопасный режим. Не запрашивайте коды из SMS/Push, ПИН, CVV/CVC и не предлагайте устанавливать приложения удаленного доступа. Сначала уточните только безопасные детали операции: карта у клиента, сумма и время.',
        }
    )


def _merge_unique_strings(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = str(item or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_unique_flags(current: list[DangerFlag], previous: list[DangerFlag]) -> list[DangerFlag]:
    out: list[DangerFlag] = []
    seen: set[tuple[str, str]] = set()
    for item in [*(current or []), *(previous or [])]:
        key = (item.type, item.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_unique_checklist(current: list[RiskChecklistItem], previous: list[RiskChecklistItem]) -> list[RiskChecklistItem]:
    out: list[RiskChecklistItem] = []
    seen: set[str] = set()
    for item in [*(current or []), *(previous or [])]:
        if item.id in seen:
            continue
        seen.add(item.id)
        out.append(item)
    return out


def _risk_rank(level: RiskLevel) -> int:
    return {RiskLevel.low: 1, RiskLevel.medium: 2, RiskLevel.high: 3}[level]


def _hydrate_analyze(history: str, raw_obj: dict | AnalyzeV1, *, prev_analyze: dict | None = None) -> AnalyzeV1:
    clean_raw = _sanitize_analyze_payload(raw_obj)
    model = AnalyzeV1.model_validate(clean_raw)
    normalized = normalize_analyze(history, model)

    if not prev_analyze:
        return normalized

    try:
        prev = AnalyzeV1.model_validate(_sanitize_analyze_payload(prev_analyze))
    except Exception:
        return normalized

    facts = normalized.facts.model_copy(
        update={
            'card_hint': normalized.facts.card_hint or prev.facts.card_hint,
            'txn_hint': normalized.facts.txn_hint or prev.facts.txn_hint,
            'amount': normalized.facts.amount if normalized.facts.amount is not None else prev.facts.amount,
            'currency': normalized.facts.currency or prev.facts.currency,
            'datetime_hint': normalized.facts.datetime_hint or prev.facts.datetime_hint,
            'merchant_hint': normalized.facts.merchant_hint or prev.facts.merchant_hint,
            'channel_hint': normalized.facts.channel_hint if normalized.facts.channel_hint != ChannelHint.unknown else prev.facts.channel_hint,
            'customer_claim': normalized.facts.customer_claim if normalized.facts.customer_claim != 'unknown' else prev.facts.customer_claim,
            'card_in_possession': normalized.facts.card_in_possession if normalized.facts.card_in_possession != 'unknown' else prev.facts.card_in_possession,
            'delivery_pref': normalized.facts.delivery_pref or prev.facts.delivery_pref,
            'previous_actions': _merge_unique_strings([*(normalized.facts.previous_actions or []), *(prev.facts.previous_actions or [])]),
            'dispute_subtype': normalized.facts.dispute_subtype if normalized.facts.dispute_subtype != DisputeSubtype.unknown else prev.facts.dispute_subtype,
            'card_state': normalized.facts.card_state if normalized.facts.card_state != CardState.unknown else prev.facts.card_state,
            'requested_actions': list(dict.fromkeys([*(normalized.facts.requested_actions or []), *(prev.facts.requested_actions or [])])),
            'status_context': normalized.facts.status_context if normalized.facts.status_context != StatusContext.unknown else prev.facts.status_context,
            'compromise_signals': list(dict.fromkeys([*(normalized.facts.compromise_signals or []), *(prev.facts.compromise_signals or [])])),
        }
    )

    risk_level = normalized.risk_level if _risk_rank(normalized.risk_level) >= _risk_rank(prev.risk_level) else prev.risk_level

    return normalized.model_copy(
        update={
            'risk_level': risk_level,
            'facts': facts,
            'profile_update': normalized.profile_update.model_copy(
                update={
                    'client_card_context': normalized.profile_update.client_card_context or prev.profile_update.client_card_context,
                    'recurring_issues': _merge_unique_strings([*(normalized.profile_update.recurring_issues or []), *(prev.profile_update.recurring_issues or [])]),
                    'notes_for_case_file': normalized.profile_update.notes_for_case_file or prev.profile_update.notes_for_case_file,
                }
            ),
            'danger_flags': _merge_unique_flags(list(normalized.danger_flags or []), list(prev.danger_flags or [])),
            'risk_checklist': _merge_unique_checklist(list(normalized.risk_checklist or []), list(prev.risk_checklist or [])),
            'analytics_tags': _merge_unique_strings([*(normalized.analytics_tags or []), *(prev.analytics_tags or [])]),
        }
    )


def _prepare_runtime_context(history: str, an_obj: dict, *, safe_mode: str, prev_analyze: dict | None = None):
    an_model = _hydrate_analyze(history, an_obj, prev_analyze=prev_analyze)
    intent = an_model.intent
    plan = build_plan(intent)
    plan = reduce_plan_after_analyze(plan, an_model)
    resolved_phase = phase_from_plan(plan)
    if an_model.phase != resolved_phase:
        an_model = an_model.model_copy(update={'phase': resolved_phase})
    tools_ui = resolve_tools(
        intent,
        resolved_phase,
        missing_fields=an_model.missing_fields,
        safe_mode=safe_mode,
        analyze=an_model,
    )

    return an_model, intent, plan, resolved_phase, list(an_model.missing_fields or []), tools_ui


def _enum_val(value):
    return getattr(value, 'value', value)


def _enum_key(value: Any) -> str:
    raw = str(_enum_val(value) or '').strip()
    return raw.split('.')[-1]


def _coerce_enum_name(value: Any, enum_cls, default: str) -> str:
    raw = _enum_key(value) or default
    try:
        return enum_cls(raw).value
    except Exception:
        return default


def _coerce_enum_list_names(values: list[Any] | None, enum_cls) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw = _enum_key(value)
        if not raw:
            continue
        try:
            normalized = enum_cls(raw).value
        except Exception:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _sanitize_analyze_payload(payload: dict | AnalyzeV1 | None) -> dict | None:
    if payload is None:
        return None

    obj = payload.model_dump() if isinstance(payload, AnalyzeV1) else dict(payload)

    obj['intent'] = _coerce_enum_name(obj.get('intent'), Intent, Intent.Unknown.value)
    obj['phase'] = _coerce_enum_name(obj.get('phase'), Phase, Phase.Collect.value)
    obj['risk_level'] = _coerce_enum_name(obj.get('risk_level'), RiskLevel, RiskLevel.medium.value)

    facts = dict(obj.get('facts') or {})
    facts['channel_hint'] = _coerce_enum_name(facts.get('channel_hint'), ChannelHint, ChannelHint.unknown.value)
    facts['dispute_subtype'] = _coerce_enum_name(facts.get('dispute_subtype'), DisputeSubtype, DisputeSubtype.unknown.value)
    facts['card_state'] = _coerce_enum_name(facts.get('card_state'), CardState, CardState.unknown.value)
    facts['status_context'] = _coerce_enum_name(facts.get('status_context'), StatusContext, StatusContext.unknown.value)
    facts['requested_actions'] = _coerce_enum_list_names(facts.get('requested_actions'), RequestedAction)
    facts['compromise_signals'] = _coerce_enum_list_names(facts.get('compromise_signals'), CompromiseSignal)

    obj['facts'] = facts
    return obj


def _build_rag_query(redacted: str, an_model: AnalyzeV1, last_customer: str | None) -> str:
    parts: list[str] = []
    summary = (an_model.summary_public or '').strip()
    if summary:
        parts.append(summary)

    intent = an_model.intent
    facts = an_model.facts
    channel_hint = str(_enum_val(getattr(facts, 'channel_hint', 'unknown')) or 'unknown')
    card_state = _enum_val(getattr(facts, 'card_state', 'unknown'))

    if intent == Intent.CardNotWorking:
        parts.append('карта не работает')
        if channel_hint == 'online':
            parts.extend([
                'онлайн-платежи',
                'интернет-платежи',
                '3ds',
                'настройки онлайн-платежей',
                'лимиты карты',
            ])
        elif channel_hint == 'pos':
            parts.extend([
                'оплата в магазине',
                'pos',
                'терминал',
                'карта не проходит',
            ])
        elif channel_hint == 'atm':
            parts.extend([
                'банкомат',
                'atm',
                'карта не читается',
            ])
        if card_state == 'damaged':
            parts.extend(['повреждение карты', 'перевыпуск'])
    elif intent == Intent.SuspiciousTransaction:
        subtype = str(_enum_val(getattr(facts, 'dispute_subtype', 'unknown')) or 'unknown')
        if subtype == 'recurring_subscription':
            parts.extend(['регулярное списание', 'подписка', 'оспаривание подписки'])
        elif subtype == 'duplicate_charge':
            parts.extend(['двойное списание', 'дубликат операции'])
        elif subtype == 'reversal_pending':
            parts.extend(['холд', 'резерв', 'незавершенное списание'])

    if last_customer:
        parts.append(last_customer.strip())
    elif redacted:
        parts.append(redacted.strip())

    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        s = str(item or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)

    return '\n'.join(out)


def _filter_sources_for_intent(intent: Intent, an_model: AnalyzeV1, sources: list[dict]) -> list[dict]:
    if not sources:
        return sources

    if intent != Intent.CardNotWorking or (an_model.facts.compromise_signals or []):
        return sources

    def _is_relevant(src: dict) -> bool:
        hay = ' '.join([
            str(src.get('title', '')),
            str(src.get('section', '')),
            str(src.get('quote', '')),
        ]).lower()

        positive = (
            'лимит', 'настройк', 'онлайн', 'интернет', '3ds',
            'магазин', 'pos', 'банкомат', 'atm',
            'не проходит', 'не работает', 'поврежден', 'повреждена', 'перевыпуск',
        )
        negative = (
            'блокировк', 'утрат', 'краж', 'компрометац',
            'мошен', 'оспарив', 'спорн', 'подозрительн',
        )

        if any(token in hay for token in positive):
            return True
        if any(token in hay for token in negative):
            return False
        return True

    filtered = [src for src in sources if _is_relevant(src)]
    return filtered or sources

def _stabilize_draft_ghost(an_obj: dict, tools_ui: list, d_obj: dict) -> dict:
    try:
        an_model = AnalyzeV1.model_validate(an_obj)
        d_model = DraftV1.model_validate(d_obj)
        d_obj = repair_draft(d_model, an_model).model_dump()
    except Exception:
        pass

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

    block_tool = next((t for t in tools_ui if t.tool.value == 'block_card'), None)
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


async def _run_analyze(redacted: str, *, safe_mode: str, cached_a: str | None, prev_analyze: dict | None):
    if cached_a:
        cached = json.loads(cached_a)
        hydrated = _hydrate_analyze(redacted, cached, prev_analyze=prev_analyze)
        return hydrated.model_dump(), True

    if safe_mode != 'ok':
        raw = llm_stub.analyze(redacted)
    else:
        raw = await llm_analyze(redacted, prev_result=prev_analyze)

    hydrated = _hydrate_analyze(redacted, raw, prev_analyze=prev_analyze)
    return hydrated.model_dump(), False
    
    
def _output_fallback(output_mod: dict[str, Any]) -> str:
    return output_mod.get('safe_text') or (
        'Понял. Уточните только безопасные детали операции. '
        'Мы не запрашиваем коды из SMS/Push, ПИН, CVV/CVC и не предлагаем удаленный доступ.'
    )


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
        d_obj = json.loads(cached_d)
        output_mod = moderate_model_output(d_obj.get('ghost_text', '') or '')
        return d_obj, True, output_mod

    an_model = AnalyzeV1.model_validate(an_obj)
    if safe_mode == 'block':
        d = _safe_draft([], plan)
    elif safe_mode == 'warn':
        d = llm_stub.draft(an_model, plan, tools_ui, [])
        d = d.model_copy(
            update={
                'ghost_text': 'Нужен безопасный режим. Не запрашивайте коды из SMS/Push, ПИН, CVV/CVC и не предлагайте удаленный доступ. Уточните только безопасные детали операции и затем переходите к следующему действию.'
            }
        )
    else:
        src_models = [SourceOut.model_validate(s) for s in sources]
        d = await llm_draft(an_model, plan, tools_ui, src_models, history=redacted)

    output_mod = moderate_model_output(d.ghost_text)
    return d.model_dump(), False, output_mod


async def run_task(task_id: str, *, worker_id: str, claimed_meta: dict | None = None):
    r = get_redis()
    meta = claimed_meta or await _claim_task(r, task_id, worker_id)
    if not meta:
        return

    if await _is_canceled(r, task_id):
        await _set_status(r, task_id, status='canceled')
        await _release_task(r, task_id, worker_id)
        return

    await _publish_status(r, task_id, meta)

    conv_id = meta['conversation_id']
    trace_id = meta['trace_id']
    actor_id = meta.get('actor_id') or 'op'

    prev_state_raw = await r.get(_state_key(conv_id))
    state_before = json.loads(prev_state_raw) if prev_state_raw else None

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(r, task_id, worker_id, heartbeat_stop))

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
            user_mod = moderate_user_input(history)
            safe_mode = user_mod['mode']
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='moderation_input',
                payload=user_mod,
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

            if await _is_canceled(r, task_id):
                raise RuntimeError('canceled')

            prev_analyze = state_before.get('last_analyze') if isinstance(state_before, dict) else None

            akey = _analyze_cache_key(conv_id, last_id)
            cached_a = await r.get(akey)
            an_obj, analyze_cached = await _run_analyze(
                redacted,
                safe_mode=safe_mode,
                cached_a=cached_a,
                prev_analyze=prev_analyze,
            )
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

            rag_cached = False
            sources: list[dict] = []
            retrieved_mod: dict[str, Any] | None = None

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
                    rag_cached = True
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
                    last_customer = None
                    for m in reversed(msgs):
                        if (m.actor_role or '').lower() != 'operator':
                            last_customer = m.content
                            break

                    an_preview = _hydrate_analyze(redacted, an_obj, prev_analyze=prev_analyze)
                    rag_q = _build_rag_query(redacted, an_preview, last_customer)
                    sources = await rag_search(db, rag_q, top_k=5)
                    sources = _filter_sources_for_intent(an_preview.intent, an_preview, sources)

                    await r.set(rkey, json.dumps(sources, ensure_ascii=False), ex=600)
                    rag_cached = False

                if sources:
                    retrieved_mod = moderate_retrieved_chunks(sources)
                    sources = retrieved_mod.get('allowed_chunks') or []
                    try:
                        an_preview = _hydrate_analyze(redacted, an_obj, prev_analyze=prev_analyze)
                        sources = _filter_sources_for_intent(an_preview.intent, an_preview, sources)
                    except Exception:
                        pass
                    if retrieved_mod.get('blocked_chunk_indices'):
                        await audit(
                            db,
                            trace_id=trace_id,
                            actor_role='operator',
                            actor_id=actor_id,
                            conversation_id=conv_id,
                            event_type='moderation_retrieved',
                            payload=retrieved_mod,
                        )
                else:
                    retrieved_mod = moderate_retrieved_chunks([])
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

            an_model, intent, plan, resolved_phase, missing_fields, tools_ui = _prepare_runtime_context(
                redacted,
                an_obj,
                safe_mode=safe_mode,
                prev_analyze=prev_analyze,
            )
            an_obj = an_model.model_dump()

            suggest_prompt_hash = make_prompt_hash(
                {
                    'conversation_id': conv_id,
                    'last_message_id': last_id,
                    'safe_mode': safe_mode,
                    'history': redacted,
                    'intent': an_obj.get('intent'),
                    'phase': resolved_phase.value,
                    'missing_fields': missing_fields,
                    'tools': [t.model_dump() for t in tools_ui],
                }
            )

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_context',
                payload={
                    'task_id': task_id,
                    'safe_mode': safe_mode,
                    'intent': intent.value,
                    'phase': resolved_phase.value,
                    'missing_fields': missing_fields,
                    'sources_count': len(sources),
                },
                retrieval_snapshot=sources,
                state_before=state_before,
                prompt_hash=suggest_prompt_hash,
                cache_info={
                    'analyze_cached': analyze_cached,
                    'rag_cached': rag_cached,
                },
            )

            dkey = _draft_cache_key(conv_id, last_id)
            cached_d = await r.get(dkey)
            draft_cached = bool(cached_d)

            d_obj, draft_cached_actual, output_mod = await _run_draft(
                redacted=redacted,
                safe_mode=safe_mode,
                an_obj=an_obj,
                plan=plan,
                tools_ui=tools_ui,
                sources=sources,
                cached_d=cached_d,
            )
            draft_cached = draft_cached_actual

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='cache_hit' if draft_cached else 'cache_miss',
                payload={'kind': 'draft'},
            )

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='moderation_output',
                payload=output_mod,
            )

            if not output_mod['ok']:
                d_obj['ghost_text'] = _output_fallback(output_mod)
                try:
                    d_obj = repair_draft(DraftV1.model_validate(d_obj), an_model).model_dump()
                except Exception:
                    pass

            await r.set(dkey, json.dumps(d_obj, ensure_ascii=False), ex=600)

            await publish(r, task_id, 'progress', {'step': 'draft', 'pct': 0.75})

            streamed = False
            streamed_buf = ''
            streamed_any = False
            src_models = [SourceOut.model_validate(s) for s in sources]

            if safe_mode == 'ok':
                try:
                    an_model = AnalyzeV1.model_validate(an_obj)
                    async for delta in stream_ghost(
                        an_model,
                        plan,
                        tools_ui,
                        history=redacted,
                        sources=src_models,
                    ):
                        if await _is_canceled(r, task_id):
                            raise RuntimeError('canceled')
                        if not delta:
                            continue
                        streamed_buf += delta
                        streamed_any = True
                        await publish(r, task_id, 'ghost_text', {'delta': delta, 'full': streamed_buf})

                    if streamed_any:
                        d_obj['ghost_text'] = streamed_buf
                        d_obj = _stabilize_draft_ghost(an_obj, tools_ui, d_obj)
                        output_mod_stream = moderate_model_output(d_obj.get('ghost_text', '') or '')
                        await audit(
                            db,
                            trace_id=trace_id,
                            actor_role='operator',
                            actor_id=actor_id,
                            conversation_id=conv_id,
                            event_type='moderation_output_stream',
                            payload=output_mod_stream,
                        )
                        if not output_mod_stream['ok']:
                            d_obj['ghost_text'] = _output_fallback(output_mod_stream)
                        output_mod = output_mod_stream
                        if d_obj.get('ghost_text', '') != streamed_buf:
                            await publish(r, task_id, 'ghost_text_final', {'full': d_obj['ghost_text']})
                        streamed = True
                except Exception:
                    streamed = False

            d_obj = _stabilize_draft_ghost(an_obj, tools_ui, d_obj)

            if not streamed:
                ghost = d_obj.get('ghost_text', '') or ''
                buf = ''
                for i in range(0, len(ghost), 40):
                    if await _is_canceled(r, task_id):
                        raise RuntimeError('canceled')
                    chunk = ghost[i:i + 40]
                    buf += chunk
                    await publish(r, task_id, 'ghost_text', {'delta': chunk, 'full': buf})

            security_summary = summarize_security_moderation(
                user_input=user_mod,
                retrieved=retrieved_mod if safe_mode == 'ok' else None,
                model_output=output_mod,
            )

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='security_summary',
                payload=security_summary,
            )

            state = {
                'conversation_id': conv_id,
                'intent': intent.value,
                'phase': resolved_phase.value,
                'plan': plan.model_dump(),
                'last_analyze': an_obj,
                'last_draft': d_obj,
            }
            await r.set(_state_key(conv_id), json.dumps(state, ensure_ascii=False), ex=86400)

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='state_persisted',
                payload={
                    'task_id': task_id,
                    'intent': intent.value,
                    'phase': resolved_phase.value,
                },
                state_before=state_before,
                state_after=state,
                prompt_hash=suggest_prompt_hash,
                cache_info={
                    'analyze_cached': analyze_cached,
                    'rag_cached': rag_cached,
                    'draft_cached': draft_cached,
                    'security_mode': security_summary['mode'],
                },
            )

            await publish(r, task_id, 'result', d_obj)
            meta = await _set_status(
                r,
                task_id,
                status='succeeded',
                extra={'error': None, 'completed_at': _now()},
            ) or meta
            await r.set(
                _task_result_key(task_id),
                json.dumps(d_obj, ensure_ascii=False),
                ex=int(settings.worker_result_ttl_sec),
            )

            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_ready',
                payload={
                    'task_id': task_id,
                    'sources_count': len(sources),
                    'mode': safe_mode,
                    'security_mode': security_summary['mode'],
                },
                retrieval_snapshot=sources,
                state_before=state_before,
                state_after=state,
                prompt_hash=suggest_prompt_hash,
                cache_info={
                    'analyze_cached': analyze_cached,
                    'rag_cached': rag_cached,
                    'draft_cached': draft_cached,
                    'streamed': streamed,
                    'security_mode': security_summary['mode'],
                },
            )
            await kafka_bus.publish(
                'copilot.suggest.v1',
                {'event': 'suggest_ready', 'task_id': task_id, 'conversation_id': conv_id, 'trace_id': trace_id},
            )

        except RuntimeError as e:
            if str(e) == 'canceled':
                await _set_status(r, task_id, status='canceled')
                await audit(
                    db,
                    trace_id=trace_id,
                    actor_role='operator',
                    actor_id=actor_id,
                    conversation_id=conv_id,
                    event_type='suggest_canceled',
                    payload={'task_id': task_id},
                    state_before=state_before,
                )
                return
            raise
        except Exception:
            err = traceback.format_exc(limit=6)
            await _set_status(r, task_id, status='failed', error=err)
            await audit(
                db,
                trace_id=trace_id,
                actor_role='operator',
                actor_id=actor_id,
                conversation_id=conv_id,
                event_type='suggest_failed',
                payload={'task_id': task_id, 'error': err},
                state_before=state_before,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await _release_task(r, task_id, worker_id)


async def _run_claimed_task(
    task_id: str,
    *,
    worker_id: str,
    claimed_meta: dict,
    semaphore: asyncio.Semaphore,
) -> None:
    try:
        await run_task(task_id, worker_id=worker_id, claimed_meta=claimed_meta)
    finally:
        semaphore.release()


async def _reclaim_loop(r, stop_event: asyncio.Event) -> None:
    interval = max(1, int(settings.worker_reclaim_interval_sec))

    while not stop_event.is_set():
        try:
            await _reclaim_expired_tasks(r)
        except Exception:
            traceback.print_exc(limit=4)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def main():
    await init_db()

    r = get_redis()
    worker_id = _worker_id()

    concurrency = max(1, int(settings.worker_concurrency))
    block_timeout = max(1, int(settings.worker_queue_block_timeout_sec))

    semaphore = asyncio.Semaphore(concurrency)
    inflight: set[asyncio.Task] = set()

    stop_event = asyncio.Event()
    reclaim_task = asyncio.create_task(_reclaim_loop(r, stop_event))

    try:
        while True:
            await semaphore.acquire()

            item = await r.blpop(_QUEUE_NAME, timeout=block_timeout)
            if not item:
                semaphore.release()
                await asyncio.sleep(0.05)
                continue

            _, task_id = item
            if isinstance(task_id, bytes):
                task_id = task_id.decode('utf-8', errors='ignore')

            task_id = str(task_id)
            claimed_meta = await _claim_task(r, task_id, worker_id)
            if not claimed_meta:
                semaphore.release()
                continue

            task = asyncio.create_task(
                _run_claimed_task(
                    task_id,
                    worker_id=worker_id,
                    claimed_meta=claimed_meta,
                    semaphore=semaphore,
                )
            )
            inflight.add(task)
            task.add_done_callback(inflight.discard)

    except asyncio.CancelledError:
        raise
    finally:
        stop_event.set()
        reclaim_task.cancel()
        with suppress(asyncio.CancelledError):
            await reclaim_task

        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
