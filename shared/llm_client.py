"""
Async client for communicating with external LLM services.

This module provides convenience wrappers around different modes of LLM
interaction defined in the project: ``analyze``, ``draft``, ``explain`` and
``stream_ghost``.  The URLs and API key are configured via the ``Settings``
class in ``shared/config.py``.  When no URL is configured for a given mode,
the functions fall back to the deterministic stub implementations found in
``shared/llm_stub.py``.  This allows running the project locally without
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

from shared.config import settings
from shared import llm_stub
from shared import llm_openai_provider


async def _post_json(url: str, payload: dict[str, Any], stream: bool = False) -> httpx.Response:
    """Internal helper to perform an HTTP POST with JSON payload.

    Includes Authorization header when ``settings.llm_api_key`` is set. For
    streaming, this helper simply performs a request with an unlimited
    timeout; the caller is responsible for iterating over the response.
    Non-streaming calls use a bounded timeout and automatically close the
    client when complete.
    """
    headers: dict[str, str] = {
        'Content-Type': 'application/json',
    }
    if settings.llm_api_key:
        headers['Authorization'] = f'Bearer {settings.llm_api_key}'

    if stream:
        # Use a long timeout for streaming; caller will close the response
        async with httpx.AsyncClient(timeout=None) as client:
            return await client.post(url, json=payload, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(url, json=payload, headers=headers)


async def analyze(history: str, prev_result: Optional[dict[str, Any]] = None) -> AnalyzeV1:
    """Perform analysis of the conversation history.

    If ``settings.llm_analyze_url`` is set, the history (and optional
    ``prev_result``) is sent to that endpoint and the JSON response is
    validated as ``AnalyzeV1``.  Otherwise, the deterministic stub is used.
    """
    url = settings.llm_analyze_url
    if not url:
        # OpenAI-compatible direct mode
        if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
            return await llm_openai_provider.analyze(history, prev_result=prev_result)

        # fallback to stub (synchronous)
        return llm_stub.analyze(history)

    payload: dict[str, Any] = {'history': history}
    if prev_result is not None:
        payload['previous_analyze'] = prev_result

    resp = await _post_json(url, payload)
    resp.raise_for_status()
    data = resp.json()
    # validate using pydantic model
    return AnalyzeV1.model_validate(data)


async def draft(
    an: AnalyzeV1,
    plan: Plan,
    tools_ui: list[ToolUI],
    sources: list[SourceOut],
    *,
    history: str = '',
) -> DraftV1:
    """Generate a draft response with ghost text and UI elements.

    When ``settings.llm_draft_url`` is set, this sends the models as JSON
    payload to that endpoint.  The response must conform to ``DraftV1``.
    Otherwise uses the stub implementation.
    """
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
    """Generate an explanation after a tool execution.

    Uses ``settings.llm_explain_url`` if present, otherwise falls back to
    the stub.  Expects the remote endpoint to return JSON matching
    ``ExplainV1``.
    """
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
) -> AsyncGenerator[str, None]:
    """Stream ghost_text tokens from the external LLM.

    If ``settings.llm_ghost_stream_url`` is configured, this function
    performs a streaming request to that endpoint and yields each token or
    chunk of text as it arrives.  When the stream completes, it stops.  If
    no streaming URL is configured, this falls back to generating the draft
    locally via the stub and splitting its ``ghost_text`` into small chunks
    (~40 characters) to simulate streaming.
    """
    url = settings.llm_ghost_stream_url
    if url:
        payload: dict[str, Any] = {
            'analyze': an.model_dump(),
            'plan': plan.model_dump(),
            'tools_ui': [t.model_dump() for t in tools_ui],
        }
        # Use a streaming POST request
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream('POST', url, json=payload, headers={
                'Authorization': f'Bearer {settings.llm_api_key}' if settings.llm_api_key else '',
                'Content-Type': 'application/json',
            }) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    if not chunk:
                        continue
                    # Assume each chunk is part of the ghost text; yield raw chunk
                    yield chunk
        return

    if (settings.llm_provider or '').lower() in ('openai_compat', 'openai', 'oai') and settings.llm_base_url:
        async for d in llm_openai_provider.stream_ghost(history, an, plan, tools_ui):
            yield d
        return
    # fallback to stub: generate draft once and stream its ghost text
    draft_obj = llm_stub.draft(an, plan, tools_ui, [])
    ghost = draft_obj.ghost_text or ''
    buf = ''
    for i in range(0, len(ghost), 40):
        buf = ghost[i:i + 40]
        yield buf
        await asyncio.sleep(0)  # yield control back to event loop