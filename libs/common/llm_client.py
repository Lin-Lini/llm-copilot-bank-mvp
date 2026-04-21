"""
Async client for communicating with external LLM services.

This module provides convenience wrappers around different modes of LLM
interaction defined in the project: ``analyze``, ``draft``, ``explain`` and
``stream_ghost``.  The URLs and API key are configured via the ``Settings``
class in ``libs/common/config.py``.  When no URL is configured for a given mode,
the functions fall back to the deterministic stub implementations found in
``libs/common/llm_stub.py``.  This allows running the project locally without
network calls while still enabling easy integration with real LLMs by
providing the appropriate environment variables.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Optional

import httpx

from contracts.schemas import (
    AnalyzeV1,
    DraftV1,
    ExplainV1,
    Plan,
    SourceOut,
    ToolUI,
)

from libs.common.config import settings
from libs.common import llm_stub
from libs.common import llm_openai_provider


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {'Content-Type': 'application/json'}
    if settings.llm_api_key:
        headers['Authorization'] = f'Bearer {settings.llm_api_key}'
    return headers


async def _post_json(url: str, payload: dict[str, Any], stream: bool = False) -> httpx.Response:
    headers = _auth_headers()
    if stream:
        async with httpx.AsyncClient(timeout=None) as client:
            return await client.post(url, json=payload, headers=headers)
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.post(url, json=payload, headers=headers)


async def analyze(history: str, prev_result: Optional[dict[str, Any]] = None) -> AnalyzeV1:
    url = settings.llm_analyze_url
    if not url:
        if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
            return await llm_openai_provider.analyze(history, prev_result=prev_result)
        return llm_stub.analyze(history)

    payload: dict[str, Any] = {'history': history}
    if prev_result is not None:
        payload['previous_analyze'] = prev_result

    resp = await _post_json(url, payload)
    resp.raise_for_status()
    data = resp.json()
    return AnalyzeV1.model_validate(data)


async def draft(
    an: AnalyzeV1,
    plan: Plan,
    tools_ui: list[ToolUI],
    sources: list[SourceOut],
    *,
    history: str = '',
) -> DraftV1:
    url = settings.llm_draft_url
    if not url:
        if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
            return await llm_openai_provider.draft(history, an, plan, tools_ui, sources)
        return llm_stub.draft(an, plan, tools_ui, sources)

    payload: dict[str, Any] = {
        'analyze': an.model_dump(),
        'plan': plan.model_dump(),
        'tools_ui': [t.model_dump() for t in tools_ui],
        'sources': [s.model_dump() for s in sources],
    }
    resp = await _post_json(url, payload)
    resp.raise_for_status()
    data = resp.json()
    return DraftV1.model_validate(data)


async def explain(tool_name: str, tool_result: dict[str, Any], plan: Plan) -> ExplainV1:
    url = settings.llm_explain_url
    if not url:
        if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
            return await llm_openai_provider.explain(tool_name, tool_result, plan)
        return llm_stub.explain(tool_name, tool_result, plan)

    payload: dict[str, Any] = {
        'tool': tool_name,
        'tool_result': tool_result,
        'plan': plan.model_dump(),
    }
    resp = await _post_json(url, payload)
    resp.raise_for_status()
    data = resp.json()
    return ExplainV1.model_validate(data)


async def stream_ghost(
    an: AnalyzeV1,
    plan: Plan,
    tools_ui: list[ToolUI],
    *,
    history: str = '',
    sources: list[SourceOut] | None = None,
) -> AsyncGenerator[str, None]:
    url = settings.llm_ghost_stream_url
    if url:
        payload: dict[str, Any] = {
            'analyze': an.model_dump(),
            'plan': plan.model_dump(),
            'tools_ui': [t.model_dump() for t in tools_ui],
            'sources': [s.model_dump() for s in (sources or [])],
            'history': history,
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream('POST', url, json=payload, headers=_auth_headers()) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    if chunk:
                        yield chunk
        return

    if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
        async for delta in llm_openai_provider.stream_ghost(history, an, plan, tools_ui, sources or []):
            yield delta
        return

    draft_obj = llm_stub.draft(an, plan, tools_ui, sources or [])
    ghost = draft_obj.ghost_text or ''
    for i in range(0, len(ghost), 40):
        yield ghost[i:i + 40]
        await asyncio.sleep(0)