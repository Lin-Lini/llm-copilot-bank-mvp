from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator

import httpx

from libs.common.config import settings


def _base(url: str) -> str:
    return url.rstrip('/')


def _auth_headers() -> dict[str, str]:
    h: dict[str, str] = {'Content-Type': 'application/json'}
    if settings.llm_api_key:
        h['Authorization'] = f'Bearer {settings.llm_api_key}'
    return h


def _extract_json_obj(text: str) -> dict[str, Any]:
    # Try direct parse first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fallback: extract first JSON object
    m = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if not m:
        raise ValueError('no_json_object')
    return json.loads(m.group(0))


async def chat_completion(*, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
    base = settings.llm_base_url or ''
    if not base:
        raise RuntimeError('LLM_BASE_URL is empty')

    url = f"{_base(base)}/chat/completions"
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        data = resp.json()

    try:
        return data['choices'][0]['message']['content'] or ''
    except Exception:
        return ''


async def chat_completion_json(*, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict[str, Any]:
    content = await chat_completion(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
    return _extract_json_obj(content)


async def chat_completion_stream(*, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> AsyncGenerator[str, None]:
    base = settings.llm_base_url or ''
    if not base:
        raise RuntimeError('LLM_BASE_URL is empty')

    url = f"{_base(base)}/chat/completions"
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': True,
    }

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream('POST', url, json=payload, headers=_auth_headers()) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # OpenAI-style SSE: "data: {...}" or "data: [DONE]"
                if line.startswith('data:'):
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj['choices'][0].get('delta', {}).get('content')
                        if delta:
                            yield delta
                    except Exception:
                        continue


async def embeddings(*, model: str, inputs: list[str]) -> list[list[float]]:
    base = settings.embed_base_url or settings.llm_base_url or ''
    if not base:
        raise RuntimeError('EMBED_BASE_URL/LLM_BASE_URL is empty')

    url = f"{_base(base)}/embeddings"
    payload: dict[str, Any] = {
        'model': model,
        'input': inputs,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        data = resp.json()

    out: list[list[float]] = []
    for item in data.get('data') or []:
        emb = item.get('embedding')
        if isinstance(emb, list):
            out.append([float(x) for x in emb])
    return out
