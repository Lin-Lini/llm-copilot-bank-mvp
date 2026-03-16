from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

from contracts.schemas import (
    AnalyzeV1,
    DraftV1,
    ExplainV1,
    ExplainUpdates,
    Phase,
    Plan,
    QuickCard,
    QuickCardKind,
    FormCard,
    FormField,
    Sidebar,
    FactsPreview,
    ToolUI,
    SourceOut,
)

from shared.config import settings
from shared.llm_stub import _risk_checklist
from shared.openai_compat import chat_completion, chat_completion_json, chat_completion_stream
from shared.prompts_ru import BASE_POLICY_RU, ANALYZE_RU, GHOST_RU, EXPLAIN_RU


def _safe_default_analyze() -> AnalyzeV1:
    # last resort fallback, but valid
    from shared.llm_stub import analyze
    return analyze('')


async def analyze(history: str, prev_result: Optional[dict[str, Any]] = None) -> AnalyzeV1:
    model = settings.llm_analyze_model or settings.llm_draft_model or settings.llm_explain_model
    if not model:
        return _safe_default_analyze()

    intents = [
        'BlockCard', 'UnblockReissue', 'LostStolen', 'SuspiciousTransaction',
        'CardNotWorking', 'StatusWhatNext', 'Unknown'
    ]
    phases = ['Collect', 'Act', 'Explain']

    sys = BASE_POLICY_RU + "\n\n" + ANALYZE_RU

    user = {
        'history': history,
        'previous_analyze': prev_result,
        'constraints': {
            'intent_enum': intents,
            'phase_enum': phases,
            'schema_version': '1.0',
        },
        'required_fields': [
            'schema_version', 'intent', 'phase', 'confidence', 'summary_public', 'risk_level',
            'facts', 'profile_update', 'missing_fields', 'next_questions',
            'tools_suggested', 'danger_flags', 'risk_checklist', 'analytics_tags'
        ]
    }

    try:
        data = await chat_completion_json(
            model=model,
            messages=[
                {'role': 'system', 'content': sys},
                {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)},
            ],
            temperature=max(0.0, float(settings.llm_temperature or 0.2)),
            max_tokens=int(settings.llm_max_tokens or 800),
        )
        return AnalyzeV1.model_validate(data)
    except Exception:
        # don't break the whole pipeline
        from shared.llm_stub import analyze as stub
        return stub(history)


def _build_quick_cards(an: AnalyzeV1) -> list[QuickCard]:
    cards: list[QuickCard] = []
    for q in (an.next_questions or [])[:6]:
        cards.append(QuickCard(title='Уточнение', insert_text=q, kind=QuickCardKind.question))

    cards.append(
        QuickCard(
            title='Безопасность',
            insert_text='Пожалуйста, не сообщайте коды из SMS/Push, CVV/CVC и ПИН. Мы этого не запрашиваем.',
            kind=QuickCardKind.instruction,
        )
    )
    return cards[:8]


def _build_form_cards(an: AnalyzeV1) -> list[FormCard]:
    if an.intent.value == 'SuspiciousTransaction':
        return [
            FormCard(
                title='Черновик обращения (create_case)',
                fields=[
                    FormField(key='intent', label='Тип обращения', value=an.intent.value),
                    FormField(key='txn_amount', label='Сумма (если подтверждено)', value=an.facts.amount),
                    FormField(key='txn_datetime', label='Дата/время (если подтверждено)', value=an.facts.datetime_hint),
                    FormField(key='customer_claim', label='Заявление клиента', value=an.facts.customer_claim),
                ],
            )
        ]
    return []


async def _ghost_text(history: str, an: AnalyzeV1, sources: list[SourceOut]) -> str:
    model = settings.llm_ghost_model or settings.llm_draft_model or settings.llm_analyze_model
    if not model:
        return ''

    sys = BASE_POLICY_RU + "\n\n" + GHOST_RU

    src = [
        {
            'title': s.title,
            'section': s.section,
            'quote': s.quote,
        } for s in (sources or [])[:6]
    ]

    user = {
        'task': 'Сформируй черновик ответа клиенту (ghost_text).',
        'analyze_summary': an.summary_public,
        'phase': an.phase.value,
        'intent': an.intent.value,
        'missing_fields': an.missing_fields,
        'next_questions': an.next_questions,
        'history': history[-4000:],
        'sources': src,
        'output_rules': {
            'language': 'ru',
            'length': '2-6 предложений',
        }
    }

    try:
        txt = await chat_completion(
            model=model,
            messages=[
                {'role': 'system', 'content': sys},
                {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)},
            ],
            temperature=max(0.0, float(settings.llm_temperature or 0.2)),
            max_tokens=int(settings.llm_max_tokens or 800),
        )
        return (txt or '').strip()
    except Exception:
        return ''


async def draft(history: str, an: AnalyzeV1, plan: Plan, tools_ui: list[ToolUI], sources: list[SourceOut]) -> DraftV1:
    ghost = await _ghost_text(history, an, sources)
    if not ghost:
        # fallback to stub draft
        from shared.llm_stub import draft as stub_d
        return stub_d(an, plan, tools_ui, sources)

    sidebar = Sidebar(
        phase=an.phase,
        intent=an.intent,
        plan=plan,
        facts_preview=FactsPreview(
            card_hint=an.facts.card_hint,
            txn_hint=an.facts.txn_hint,
            amount=an.facts.amount,
            datetime_hint=an.facts.datetime_hint,
            merchant_hint=an.facts.merchant_hint,
        ),
        sources=sources,
        tools=tools_ui,
        risk_checklist=an.risk_checklist or _risk_checklist(),
        danger_flags=an.danger_flags,
        operator_notes='Используй найденные регламенты/скрипты как основу. Если не хватает данных, добери уточнения.'
    )

    return DraftV1(
        schema_version='1.0',
        ghost_text=ghost,
        quick_cards=_build_quick_cards(an),
        form_cards=_build_form_cards(an),
        sidebar=sidebar,
    )


def _result_is_error(tool_result: dict[str, Any]) -> bool:
    if not isinstance(tool_result, dict):
        return True
    if tool_result.get("error"):
        return True
    ok = tool_result.get("ok")
    if ok is False:
        return True
    return False


def _sanitize_explain_updates(plan: Plan, proposed: ExplainV1) -> ExplainUpdates:
    allowed_ids = [s.id for s in plan.steps]
    allowed_set = set(allowed_ids)

    # 1) done-map: берем только существующие шаги, не даем "сделать false", только false->true
    proposed_done: dict[str, bool] = {}
    try:
        for s in (proposed.updates.plan.steps or []):
            if s.id in allowed_set:
                proposed_done[s.id] = bool(s.done)
    except Exception:
        proposed_done = {}

    new_steps = []
    for s in plan.steps:
        done = s.done or proposed_done.get(s.id, False)
        new_steps.append(s.model_copy(update={"done": done}))

    tmp_plan = plan.model_copy(update={"steps": new_steps})

    # 2) current_step_id: только из списка шагов, иначе ставим "первый незавершенный"
    proposed_current = getattr(proposed.updates.plan, "current_step_id", None)
    if proposed_current not in allowed_set:
        proposed_current = None

    if proposed_current:
        current_step_id = proposed_current
    else:
        current_step_id = next((s.id for s in tmp_plan.steps if not s.done), tmp_plan.current_step_id)

    new_plan = tmp_plan.model_copy(update={"current_step_id": current_step_id})

    # 3) phase: только enum, иначе fallback по плану
    proposed_phase = proposed.updates.phase if proposed and proposed.updates else None
    if proposed_phase not in (Phase.Collect, Phase.Act, Phase.Explain):
        proposed_phase = Phase.Act if any(not s.done for s in new_plan.steps) else Phase.Explain

    return ExplainUpdates(phase=proposed_phase, plan=new_plan)

async def explain(tool_name: str, tool_result: dict[str, Any], plan: Plan) -> ExplainV1:
    model = settings.llm_explain_model or settings.llm_draft_model or settings.llm_analyze_model
    from shared.llm_stub import explain as stub_e
    from shared.llm_stub import _risk_checklist as _rc

    # Бэковый fallback: работает всегда и не ломает state machine
    def _fallback() -> ExplainV1:
        ex = stub_e(tool_name, tool_result, plan)
        # чуть умнее фаза: если ошибка → Collect, если остались шаги → Act, иначе Explain
        if _result_is_error(tool_result):
            phase = Phase.Collect
        else:
            phase = Phase.Act if any(not s.done for s in ex.updates.plan.steps) else Phase.Explain
        ex.updates = ex.updates.model_copy(update={"phase": phase})
        return ex

    if not model:
        return _fallback()

    sys = BASE_POLICY_RU + "\n\n" + EXPLAIN_RU

    user = {
        "tool": tool_name,
        "tool_result": tool_result,
        "plan": plan.model_dump(),
        "constraints": {
            "schema_version": "1.0",
            "phase_enum": ["Collect", "Act", "Explain"],
            "allowed_step_ids": [s.id for s in plan.steps],
        },
        "notes": [
            "НЕ добавляй новые шаги в plan.steps.",
            "current_step_id должен быть одним из allowed_step_ids.",
            "Не обещай возврат/компенсацию. Не упоминай внутренние tool-имена.",
        ],
    }

    try:
        data = await chat_completion_json(
            model=model,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=max(0.0, float(settings.llm_temperature or 0.2)),
            max_tokens=int(settings.llm_max_tokens or 800),
        )

        proposed = ExplainV1.model_validate(data)

        # Страховка бэка: обновления плана/фазы приводим к допустимому виду
        safe_updates = _sanitize_explain_updates(plan, proposed)

        # Если tool упал, а LLM поставила Act/Explain — режем в Collect
        if _result_is_error(tool_result):
            safe_updates = safe_updates.model_copy(update={"phase": Phase.Collect})

        # Финальная сборка: текст/карточки от LLM, plan/phase — нормализованы
        return ExplainV1(
            schema_version="1.0",
            ghost_text=(proposed.ghost_text or "").strip(),
            updates=safe_updates,
            quick_cards=proposed.quick_cards or [],
            result_summary_public=(proposed.result_summary_public or "Инструмент выполнен; оператору показаны следующие шаги.").strip(),
            danger_flags=proposed.danger_flags or [],
            risk_checklist=proposed.risk_checklist or _rc(),
        )
    except Exception:
        return _fallback()

async def stream_ghost(history: str, an: AnalyzeV1, plan: Plan, tools_ui: list[ToolUI], sources: list[SourceOut]) -> AsyncGenerator[str, None]:
    model = settings.llm_ghost_model or settings.llm_draft_model
    if not model:
        return

    sys = BASE_POLICY_RU + "\n\n" + GHOST_RU + "\n\n(стриминг: отдавай токены частями)"
    user = {
        'task': 'Сгенерируй ghost_text (стрим).',
        'analyze_summary': an.summary_public,
        'phase': an.phase.value,
        'intent': an.intent.value,
        'missing_fields': an.missing_fields,
        'next_questions': an.next_questions,
        'history': history[-4000:],
        'sources': [{'title': s.title, 'section': s.section, 'quote': s.quote} for s in (sources or [])][:6],
    }

    async for delta in chat_completion_stream(
        model=model,
        messages=[
            {'role': 'system', 'content': sys},
            {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)},
        ],
        temperature=max(0.0, float(settings.llm_temperature or 0.2)),
        max_tokens=int(settings.llm_max_tokens or 800),
    ):
        yield delta
