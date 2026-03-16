from __future__ import annotations

import hashlib
import math
from typing import Iterable

from libs.common.config import settings
from libs.common.embed import embed64
from libs.common.openai_compat import embeddings as openai_embeddings


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _project_hash(vec: list[float], out_dim: int, seed: str = 'rag') -> list[float]:
    # Sparse random projection without storing matrix.
    # Deterministic: each input dim maps to a bucket with +/- sign.
    if out_dim <= 0:
        return []
    out = [0.0] * out_dim
    for i, v in enumerate(vec):
        if v == 0.0:
            continue
        h = hashlib.sha256(f'{seed}:{i}'.encode('utf-8')).digest()
        idx = int.from_bytes(h[:4], 'little') % out_dim
        sign = -1.0 if (h[4] & 1) else 1.0
        out[idx] += sign * float(v)
    return _normalize(out)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    # default stub
    provider = (settings.embed_provider or 'stub').lower()
    dim = int(settings.rag_dim or 64)

    if provider in ('stub', 'hash', 'embed64'):
        return [embed64(t) for t in texts]

    if provider in ('openai_compat', 'openai', 'oai'):
        model = settings.embed_model or 'text-embedding-3-small'
        raw = await openai_embeddings(model=model, inputs=texts)
        out: list[list[float]] = []
        for v in raw:
            if len(v) == dim:
                out.append(_normalize([float(x) for x in v]))
            else:
                out.append(_project_hash([float(x) for x in v], dim))
        return out

    # fallback
    return [embed64(t) for t in texts]


async def embed_text(text: str) -> list[float]:
    res = await embed_texts([text])
    return res[0] if res else [0.0] * int(settings.rag_dim or 64)
