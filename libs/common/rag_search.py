from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.embeddings import embed_text
from libs.common.models import RagChunk
from libs.common.rag_docs import section_priority


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


def _score_item(item: dict[str, Any]) -> float:
    source_boost = float(item.get('source_priority') or 1.0)
    section_boost = section_priority(item.get('section') or '')
    rel = 0.58 * float(item.get('lex_sim', 0.0)) + 0.42 * float(item.get('sem_sim', 0.0))
    rel *= source_boost * section_boost
    return min(1.0, max(0.0, rel))


async def hybrid_search(db: AsyncSession, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    q = (query or '').strip()
    if not q:
        return []

    top_k = max(1, min(int(top_k or 5), 10))

    qvec = await embed_text(q)
    dist = RagChunk.embedding.l2_distance(qvec).label('dist')
    sem_rows = (await db.execute(
        select(RagChunk, dist)
        .order_by(dist)
        .limit(top_k * 4)
    )).all()

    items: dict[tuple[str, str], dict[str, Any]] = {}
    for r, d in sem_rows:
        key = (r.doc_id, r.section)
        sim = 1.0 / (1.0 + float(d or 0.0))
        items[key] = {
            'doc_id': r.doc_id,
            'title': r.title,
            'doc_code': r.doc_code,
            'source_type': r.source_type,
            'source_priority': float(r.source_priority or 1.0),
            'section': r.section,
            'quote': _clip_quote(r.text),
            'sem_sim': sim,
            'lex_sim': 0.0,
        }

    try:
        tsv = func.to_tsvector('russian', RagChunk.text)
        tsq = func.websearch_to_tsquery('russian', q)
    except Exception:
        tsv = func.to_tsvector('simple', RagChunk.text)
        tsq = func.websearch_to_tsquery('simple', q)

    rank = func.ts_rank_cd(tsv, tsq).label('rank')
    lex_rows = (await db.execute(
        select(RagChunk, rank)
        .where(tsv.op('@@')(tsq))
        .order_by(rank.desc())
        .limit(top_k * 6)
    )).all()

    lex_scores = [float(rk or 0.0) for _, rk in lex_rows]
    lex_norm = _norm_scores(lex_scores)
    for (r, _rk), ln in zip(lex_rows, lex_norm):
        key = (r.doc_id, r.section)
        base = items.setdefault(key, {
            'doc_id': r.doc_id,
            'title': r.title,
            'doc_code': r.doc_code,
            'source_type': r.source_type,
            'source_priority': float(r.source_priority or 1.0),
            'section': r.section,
            'quote': _clip_quote(r.text),
            'sem_sim': 0.0,
            'lex_sim': 0.0,
        })
        base['lex_sim'] = max(float(base.get('lex_sim', 0.0)), float(ln))

    merged: list[dict[str, Any]] = []
    for item in items.values():
        rel = _score_item(item)
        merged.append({
            'doc_id': item['doc_id'],
            'title': item['title'],
            'section': item['section'],
            'quote': item['quote'],
            'relevance': rel,
            'source_type': item['source_type'],
            'doc_code': item['doc_code'],
        })

    merged.sort(key=lambda x: (x['relevance'], x['doc_id'], x['section']), reverse=True)

    final: list[dict[str, Any]] = []
    used_docs: dict[str, int] = {}
    used_quotes: set[str] = set()
    for item in merged:
        quote_key = (item.get('quote') or '').strip().lower()
        if not quote_key:
            continue
        if quote_key in used_quotes:
            continue
        doc_id = item['doc_id']
        if used_docs.get(doc_id, 0) >= 2:
            continue
        used_quotes.add(quote_key)
        used_docs[doc_id] = used_docs.get(doc_id, 0) + 1
        final.append({
            'doc_id': item['doc_id'],
            'title': item['title'],
            'section': item['section'],
            'quote': item['quote'],
            'relevance': item['relevance'],
        })
        if len(final) >= top_k:
            break

    return final
