from __future__ import annotations

import hashlib
import math


def embed64(text: str) -> list[float]:
    # детерминированный эмбеддинг (MVP): токены -> хэш -> проекция в 64-d
    dim = 64
    vec = [0.0] * dim
    toks = [t for t in text.lower().split() if t]
    if not toks:
        return vec

    for t in toks[:512]:
        h = hashlib.sha256(t.encode('utf-8')).digest()
        for i in range(dim):
            b = h[i % len(h)]
            sign = -1.0 if (b & 1) else 1.0
            vec[i] += sign * (1.0 + (b / 255.0))

    # normalize
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
