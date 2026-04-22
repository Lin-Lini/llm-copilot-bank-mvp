from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.embeddings import embed_text
from libs.common.models import RagChunk
from libs.common.rag_docs import section_priority
from libs.common.rag_planner import PlannedQuery, build_search_queries, significant_terms


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


def _apply_filters(stmt, pq: PlannedQuery):
    if pq.source_types:
        stmt = stmt.where(RagChunk.source_type.in_(list(pq.source_types)))
    if pq.doc_code:
        stmt = stmt.where(RagChunk.doc_code == pq.doc_code)
    return stmt


def _quote_coverage(user_terms: list[str], item: dict[str, Any]) -> float:
    if not user_terms:
        return 0.0
    hay = ' '.join(
        [
            str(item.get('title') or ''),
            str(item.get('section') or ''),
            str(item.get('section_path') or ''),
            str(item.get('quote') or ''),
        ]
    ).lower()
    hits = sum(1 for term in user_terms if term in hay)
    return hits / max(1, len(user_terms))


def _risk_overlap(item: dict[str, Any], pq: PlannedQuery) -> bool:
    item_tags = {x.strip() for x in str(item.get('risk_tags') or '').split(',') if x.strip()}
    return bool(item_tags & set(pq.risk_tags))


def _chunk_type_boost(item: dict[str, Any], pq: PlannedQuery) -> float:
    chunk_type = str(item.get('chunk_type') or '')
    if chunk_type and chunk_type in set(pq.prefer_chunk_types):
        return 1.08
    if chunk_type == 'warning':
        return 1.04
    if chunk_type == 'checklist':
        return 1.03
    return 1.0


def _source_type_boost(item: dict[str, Any], pq: PlannedQuery) -> float:
    source_type = str(item.get('source_type') or '')
    boost = 1.0

    if source_type == 'security':
        boost *= 1.05

    if pq.label == 'safety':
        if source_type == 'security':
            boost *= 1.28
        elif source_type == 'policy':
            boost *= 1.10
        elif source_type == 'script':
            boost *= 0.97
        else:
            boost *= 0.92
    elif pq.label == 'lost_stolen':
        if source_type == 'security':
            boost *= 1.26
        elif source_type in {'policy', 'procedure'}:
            boost *= 1.10
    elif pq.label == 'dispute':
        if source_type in {'policy', 'procedure'}:
            boost *= 1.10
        elif source_type == 'security':
            boost *= 1.05
    elif pq.label == 'fallback':
        if source_type == 'fallback':
            boost *= 1.35
        elif source_type in {'procedure', 'policy'}:
            boost *= 1.12
        elif source_type == 'script':
            boost *= 1.04
        elif source_type == 'security':
            boost *= 0.90
    elif pq.label == 'script':
        if source_type == 'script':
            boost *= 1.14
        elif source_type == 'security':
            boost *= 1.03
        else:
            boost *= 0.98
    elif pq.label == 'status':
        if source_type in {'policy', 'procedure'}:
            boost *= 1.08
    elif pq.label == 'card_ops':
        if source_type in {'security', 'policy', 'procedure'}:
            boost *= 1.06

    return boost


def _section_signal_boost(item: dict[str, Any], pq: PlannedQuery) -> float:
    chunk_type = str(item.get('chunk_type') or '')
    hay = ' '.join(
        [
            str(item.get('title') or ''),
            str(item.get('section') or ''),
            str(item.get('section_path') or ''),
            str(item.get('quote') or ''),
        ]
    ).lower()

    boost = 1.0

    if pq.label == 'safety':
        if any(x in hay for x in ('безопас', 'sms', 'смс', 'push', 'пин', 'pin', 'cvv', 'cvc', 'социнж', 'мошен', 'код')):
            boost *= 1.12
        if chunk_type in {'warning', 'checklist', 'condition'}:
            boost *= 1.08

    if pq.label == 'lost_stolen':
        if any(x in hay for x in ('утрат', 'краж', 'украд', 'потер', 'блокиров', 'компрометац')):
            boost *= 1.14
        if any(x in hay for x in ('карту нужно заблокировать', 'немедленную блокировку', 'блокировка карты')):
            boost *= 1.10
        if chunk_type in {'step', 'condition', 'checklist', 'warning'}:
            boost *= 1.06

    if pq.label == 'dispute':
        if any(x in hay for x in ('оспар', 'диспут', 'спорн', 'списан', 'чарджбэк', 'chargeback')):
            boost *= 1.10
        if chunk_type in {'step', 'condition', 'checklist'}:
            boost *= 1.04

    if pq.label == 'fallback':
        if source_type := str(item.get('source_type') or ''):
            if source_type == 'fallback':
                boost *= 1.18
        if any(x in hay for x in ('недоступ', 'fallback', 'фолбэк', 'резерв', 'временно', 'инструмент', 'сервис')):
            boost *= 1.18
        if chunk_type in {'step', 'condition', 'paragraph', 'checklist'}:
            boost *= 1.05

    if pq.label == 'status':
        if any(x in hay for x in ('статус', 'sla', 'эскалац', 'срок', 'ожидание')):
            boost *= 1.08

    return boost


def _latest_doc_map(items: list[dict[str, Any]]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for item in items:
        code = str(item.get('doc_code') or '')
        date = str(item.get('effective_date') or '')
        if not code or not date:
            continue
        if date > latest.get(code, ''):
            latest[code] = date
    return latest


def _base_candidate(row: RagChunk) -> dict[str, Any]:
    return {
        'id': row.id,
        'doc_id': row.doc_id,
        'title': row.title,
        'doc_code': row.doc_code,
        'version_label': row.version_label,
        'effective_date': row.effective_date,
        'source_type': row.source_type,
        'source_priority': float(row.source_priority or 1.0),
        'section': row.section,
        'section_path': row.section_path,
        'chunk_type': row.chunk_type,
        'risk_tags': row.risk_tags,
        'is_mandatory_step': bool(row.is_mandatory_step),
        'quote': _clip_quote(row.text),
        'score': 0.0,
        'sem_sim': 0.0,
        'lex_sim': 0.0,
        'matched_queries': [],
    }


async def _semantic_rows(db: AsyncSession, pq: PlannedQuery, *, limit: int):
    qvec = await embed_text(pq.query)
    dist = RagChunk.embedding.l2_distance(qvec).label('dist')
    stmt = (
        select(RagChunk, dist)
        .order_by(dist)
        .limit(limit)
    )
    stmt = _apply_filters(stmt, pq)
    return (await db.execute(stmt)).all()


async def _lexical_rows(db: AsyncSession, pq: PlannedQuery, *, limit: int):
    try:
        tsv = func.to_tsvector('russian', RagChunk.text)
        tsq = func.websearch_to_tsquery('russian', pq.query)
    except Exception:
        tsv = func.to_tsvector('simple', RagChunk.text)
        tsq = func.websearch_to_tsquery('simple', pq.query)

    rank = func.ts_rank_cd(tsv, tsq).label('rank')
    stmt = (
        select(RagChunk, rank)
        .where(tsv.op('@@')(tsq))
        .order_by(rank.desc())
        .limit(limit)
    )
    stmt = _apply_filters(stmt, pq)
    return (await db.execute(stmt)).all()


def _partial_score(item: dict[str, Any], pq: PlannedQuery) -> float:
    rel = 0.52 * float(item.get('lex_sim', 0.0)) + 0.48 * float(item.get('sem_sim', 0.0))
    rel *= float(item.get('source_priority') or 1.0)
    rel *= section_priority(item.get('section') or '')
    rel *= _chunk_type_boost(item, pq)
    rel *= _source_type_boost(item, pq)
    rel *= _section_signal_boost(item, pq)

    if _risk_overlap(item, pq):
        rel *= 1.08
    if item.get('is_mandatory_step'):
        rel *= 1.04

    return max(0.0, rel * pq.weight)


def _query_needs_security_coverage(plans: list[PlannedQuery]) -> bool:
    return any(
        pq.label in {'safety', 'lost_stolen'} or 'security' in set(pq.risk_tags or ())
        for pq in plans
    )


def _can_take_item(
    item: dict[str, Any],
    *,
    used_docs: dict[str, int],
    used_quotes: set[str],
) -> bool:
    quote_key = (item.get('quote') or '').strip().lower()
    if not quote_key:
        return False
    if quote_key in used_quotes:
        return False

    doc_key = str(item.get('doc_code') or item.get('doc_id') or '')
    if used_docs.get(doc_key, 0) >= 2:
        return False

    return True


def _take_item(
    final: list[dict[str, Any]],
    item: dict[str, Any],
    rel: float,
    *,
    used_docs: dict[str, int],
    used_quotes: set[str],
) -> None:
    quote_key = (item.get('quote') or '').strip().lower()
    doc_key = str(item.get('doc_code') or item.get('doc_id') or '')

    used_quotes.add(quote_key)
    used_docs[doc_key] = used_docs.get(doc_key, 0) + 1

    final.append(
        {
            'doc_id': item['doc_id'],
            'title': item['title'],
            'section': item.get('section_path') or item.get('section') or '',
            'quote': item['quote'],
            'relevance': rel,
        }
    )


def _select_final_results(ranked: list[dict[str, Any]], *, top_k: int, security_needed: bool) -> list[dict[str, Any]]:
    norm = _norm_scores([float(item.get('rerank') or 0.0) for item in ranked])

    by_id: dict[int, float] = {
        int(item['id']): rel
        for item, rel in zip(ranked, norm)
    }

    final: list[dict[str, Any]] = []
    used_docs: dict[str, int] = {}
    used_quotes: set[str] = set()

    if security_needed and ranked:
        best_score = float(ranked[0].get('rerank') or 0.0)
        security_candidates = [item for item in ranked if str(item.get('source_type') or '') == 'security']
        if security_candidates:
            best_security = security_candidates[0]
            if float(best_security.get('rerank') or 0.0) >= best_score * 0.50:
                if _can_take_item(best_security, used_docs=used_docs, used_quotes=used_quotes):
                    _take_item(
                        final,
                        best_security,
                        by_id.get(int(best_security['id']), 0.0),
                        used_docs=used_docs,
                        used_quotes=used_quotes,
                    )

    for item in ranked:
        if len(final) >= top_k:
            break
        if not _can_take_item(item, used_docs=used_docs, used_quotes=used_quotes):
            continue
        _take_item(
            final,
            item,
            by_id.get(int(item['id']), 0.0),
            used_docs=used_docs,
            used_quotes=used_quotes,
        )

    return final


async def hybrid_search(db: AsyncSession, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    q = (query or '').strip()
    if not q:
        return []

    top_k = max(1, min(int(top_k or 5), 10))
    plans = build_search_queries(q)
    if not plans:
        return []

    candidates: dict[int, dict[str, Any]] = {}

    for pq in plans:
        sem_rows = await _semantic_rows(db, pq, limit=top_k * 4)
        for row, dist in sem_rows:
            item = candidates.setdefault(row.id, _base_candidate(row))
            item['sem_sim'] = max(item['sem_sim'], 1.0 / (1.0 + float(dist or 0.0)))
            item['matched_queries'].append(pq.label)

        lex_rows = await _lexical_rows(db, pq, limit=top_k * 6)
        lex_scores = [float(rank or 0.0) for _, rank in lex_rows]
        lex_norm = _norm_scores(lex_scores)

        for (row, _rank), ln in zip(lex_rows, lex_norm):
            item = candidates.setdefault(row.id, _base_candidate(row))
            item['lex_sim'] = max(item['lex_sim'], float(ln))
            item['matched_queries'].append(pq.label)

        for item in candidates.values():
            if pq.label not in item['matched_queries']:
                continue
            part = _partial_score(item, pq)
            item['score'] = max(item['score'], part) + 0.08 * part

    if not candidates:
        return []

    all_items = list(candidates.values())
    latest_by_code = _latest_doc_map(all_items)
    user_terms = significant_terms(q, limit=8)
    security_needed = _query_needs_security_coverage(plans)

    for item in all_items:
        score = float(item.get('score') or 0.0)

        latest_date = latest_by_code.get(str(item.get('doc_code') or ''), '')
        item_date = str(item.get('effective_date') or '')
        if latest_date and item_date:
            if item_date == latest_date:
                score *= 1.04
            else:
                score *= 0.95

        coverage = _quote_coverage(user_terms, item)
        score *= 0.90 + 0.22 * coverage

        matched = set(item.get('matched_queries') or [])
        score *= 1.0 + min(0.12, 0.03 * max(0, len(matched) - 1))

        if security_needed and str(item.get('source_type') or '') == 'security':
            score *= 1.08

        if 'fallback' in matched and str(item.get('source_type') or '') == 'fallback':
            score *= 1.18

        item['rerank'] = score

    ranked = sorted(
        all_items,
        key=lambda x: (
            float(x.get('rerank') or 0.0),
            x.get('source_priority') or 1.0,
            x['doc_id'],
            x['section'],
        ),
        reverse=True,
    )

    return _select_final_results(ranked, top_k=top_k, security_needed=security_needed)