from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.embeddings import embed_text
from shared.models import RagChunk


def _clip_quote(t: str, n: int = 200) -> str:
    t = (t or '').strip()
    return t[:n]


def _norm_scores(xs: list[float]) -> list[float]:
    if not xs:
        return []
    mx = max(xs)
    if mx <= 0:
        return [0.0 for _ in xs]
    return [min(1.0, max(0.0, x / mx)) for x in xs]


async def hybrid_search(db: AsyncSession, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    q = (query or '').strip()
    if not q:
        return []

    top_k = max(1, min(int(top_k or 5), 10))

    # semantic
    qvec = await embed_text(q)
    dist = RagChunk.embedding.l2_distance(qvec).label('dist')
    sem_rows = (await db.execute(
        select(RagChunk, dist)
        .order_by(dist)
        .limit(top_k)
    )).all()

    sem_keys: list[tuple[str, str]] = []
    sem_sims: list[float] = []
    sem_items: dict[tuple[str, str], dict[str, Any]] = {}
    for r, d in sem_rows:
        key = (r.doc_id, r.section)
        sem_keys.append(key)
        # similarity in (0,1]
        sim = 1.0 / (1.0 + float(d or 0.0))
        sem_sims.append(sim)
        sem_items[key] = {
            'doc_id': r.doc_id,
            'title': r.title,
            'section': r.section,
            'quote': _clip_quote(r.text),
            'sem_sim': sim,
            'lex_sim': 0.0,
        }

    # lexical (Postgres full-text). Russian works well for regs/scripts.
    try:
        tsv = func.to_tsvector('russian', RagChunk.text)
        tsq = func.plainto_tsquery('russian', q)
    except Exception:
        tsv = func.to_tsvector('simple', RagChunk.text)
        tsq = func.plainto_tsquery('simple', q)

    rank = func.ts_rank_cd(tsv, tsq).label('rank')
    lex_stmt = (
        select(RagChunk, rank)
        .where(tsv.op('@@')(tsq))
        .order_by(rank.desc())
        .limit(top_k)
    )
    lex_rows = (await db.execute(lex_stmt)).all()

    lex_scores: list[float] = [float(rk or 0.0) for _, rk in lex_rows]
    lex_norm = _norm_scores(lex_scores)

    for (r, rk), ln in zip(lex_rows, lex_norm):
        key = (r.doc_id, r.section)
        if key not in sem_items:
            sem_items[key] = {
                'doc_id': r.doc_id,
                'title': r.title,
                'section': r.section,
                'quote': _clip_quote(r.text),
                'sem_sim': 0.0,
                'lex_sim': ln,
            }
        else:
            sem_items[key]['lex_sim'] = max(sem_items[key]['lex_sim'], ln)

    # merge scores
    merged: list[dict[str, Any]] = []
    for item in sem_items.values():
        rel = 0.6 * float(item.get('lex_sim', 0.0)) + 0.4 * float(item.get('sem_sim', 0.0))
        rel = min(1.0, max(0.0, rel))
        merged.append({
            'doc_id': item['doc_id'],
            'title': item['title'],
            'section': item['section'],
            'quote': item['quote'],
            'relevance': rel,
        })

    merged.sort(key=lambda x: x['relevance'], reverse=True)
    return merged[:top_k]
