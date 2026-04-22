from __future__ import annotations

import math
from typing import Any


def _norm(text: str | None) -> str:
    return ' '.join((text or '').lower().split())


def result_matches_target(result: dict[str, Any], target: dict[str, Any]) -> bool:
    title = _norm(str(result.get('title') or ''))
    section = _norm(str(result.get('section') or ''))
    quote = _norm(str(result.get('quote') or ''))

    title_contains = _norm(str(target.get('title_contains') or ''))
    section_contains = _norm(str(target.get('section_contains') or ''))
    quote_contains = _norm(str(target.get('quote_contains') or ''))

    if title_contains and title_contains not in title:
        return False
    if section_contains and section_contains not in section:
        return False
    if quote_contains and quote_contains not in quote:
        return False
    return True


def result_is_relevant(result: dict[str, Any], expected_any: list[dict[str, Any]]) -> bool:
    return any(result_matches_target(result, target) for target in expected_any)


def relevance_vector(results: list[dict[str, Any]], expected_any: list[dict[str, Any]], *, k: int) -> list[int]:
    out: list[int] = []
    for row in results[:k]:
        out.append(1 if result_is_relevant(row, expected_any) else 0)
    while len(out) < k:
        out.append(0)
    return out


def recall_at_k(results: list[dict[str, Any]], expected_any: list[dict[str, Any]], *, k: int) -> float:
    if not expected_any:
        return 0.0

    matched = 0
    for target in expected_any:
        if any(result_matches_target(row, target) for row in results[:k]):
            matched += 1
    return matched / len(expected_any)


def reciprocal_rank(results: list[dict[str, Any]], expected_any: list[dict[str, Any]], *, k: int) -> float:
    for idx, row in enumerate(results[:k], start=1):
        if result_is_relevant(row, expected_any):
            return 1.0 / idx
    return 0.0


def ndcg_at_k(results: list[dict[str, Any]], expected_any: list[dict[str, Any]], *, k: int) -> float:
    rel = relevance_vector(results, expected_any, k=k)
    dcg = 0.0
    for i, r in enumerate(rel, start=1):
        if r:
            dcg += 1.0 / math.log2(i + 1)

    ideal_hits = min(k, len(expected_any))
    if ideal_hits <= 0:
        return 0.0

    idcg = 0.0
    for i in range(1, ideal_hits + 1):
        idcg += 1.0 / math.log2(i + 1)

    return dcg / idcg if idcg > 0 else 0.0


def security_coverage_at_k(results: list[dict[str, Any]], expected_security_any: list[dict[str, Any]], *, k: int) -> float | None:
    if not expected_security_any:
        return None
    return recall_at_k(results, expected_security_any, k=k)


def evaluate_entry(entry: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    top_k = int(entry.get('top_k') or 5)
    expected_any = list(entry.get('expected_any') or [])
    expected_security_any = list(entry.get('expected_security_any') or [])

    recall = recall_at_k(results, expected_any, k=top_k)
    mrr = reciprocal_rank(results, expected_any, k=top_k)
    ndcg = ndcg_at_k(results, expected_any, k=top_k)
    hits = relevance_vector(results, expected_any, k=top_k)

    sec_cov = security_coverage_at_k(results, expected_security_any, k=top_k)
    sec_passed = None if sec_cov is None else sec_cov > 0.0

    return {
        'name': entry.get('name') or entry.get('query') or 'unnamed',
        'query': entry.get('query') or '',
        'top_k': top_k,
        'expected_any': expected_any,
        'expected_security_any': expected_security_any,
        'hits': hits,
        'recall_at_k': recall,
        'mrr': mrr,
        'ndcg_at_k': ndcg,
        'security_coverage_at_k': sec_cov,
        'security_passed': sec_passed,
        'passed': any(hits),
        'results': results[:top_k],
        'notes': entry.get('notes') or '',
    }


def aggregate_reports(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            'queries': 0,
            'pass_rate': 0.0,
            'avg_recall_at_k': 0.0,
            'avg_mrr': 0.0,
            'avg_ndcg_at_k': 0.0,
            'security_queries': 0,
            'security_pass_rate': 0.0,
            'avg_security_coverage_at_k': 0.0,
        }

    n = len(items)
    passed = sum(1 for item in items if item.get('passed'))
    avg_recall = sum(float(item.get('recall_at_k') or 0.0) for item in items) / n
    avg_mrr = sum(float(item.get('mrr') or 0.0) for item in items) / n
    avg_ndcg = sum(float(item.get('ndcg_at_k') or 0.0) for item in items) / n

    sec_items = [item for item in items if item.get('security_coverage_at_k') is not None]
    sec_n = len(sec_items)
    sec_passed = sum(1 for item in sec_items if item.get('security_passed'))
    avg_sec_cov = (
        sum(float(item.get('security_coverage_at_k') or 0.0) for item in sec_items) / sec_n
        if sec_n
        else 0.0
    )

    return {
        'queries': n,
        'pass_rate': passed / n,
        'avg_recall_at_k': avg_recall,
        'avg_mrr': avg_mrr,
        'avg_ndcg_at_k': avg_ndcg,
        'security_queries': sec_n,
        'security_pass_rate': (sec_passed / sec_n) if sec_n else 0.0,
        'avg_security_coverage_at_k': avg_sec_cov,
    }


def render_markdown_report(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append('# RAG Evaluation Report')
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append(f"- Queries: {summary['queries']}")
    lines.append(f"- Pass rate: {summary['pass_rate']:.3f}")
    lines.append(f"- Avg Recall@k: {summary['avg_recall_at_k']:.3f}")
    lines.append(f"- Avg MRR: {summary['avg_mrr']:.3f}")
    lines.append(f"- Avg nDCG@k: {summary['avg_ndcg_at_k']:.3f}")
    lines.append(f"- Security queries: {summary['security_queries']}")
    lines.append(f"- Security pass rate: {summary['security_pass_rate']:.3f}")
    lines.append(f"- Avg Security coverage@k: {summary['avg_security_coverage_at_k']:.3f}")
    lines.append('')

    lines.append('## Per-query results')
    lines.append('')
    for item in items:
        lines.append(f"### {item['name']}")
        lines.append('')
        lines.append(f"- Query: `{item['query']}`")
        lines.append(f"- top_k: {item['top_k']}")
        lines.append(f"- Passed: {'yes' if item['passed'] else 'no'}")
        lines.append(f"- Recall@k: {item['recall_at_k']:.3f}")
        lines.append(f"- MRR: {item['mrr']:.3f}")
        lines.append(f"- nDCG@k: {item['ndcg_at_k']:.3f}")
        if item.get('security_coverage_at_k') is not None:
            lines.append(f"- Security coverage@k: {float(item['security_coverage_at_k']):.3f}")
            lines.append(f"- Security passed: {'yes' if item.get('security_passed') else 'no'}")
        if item.get('notes'):
            lines.append(f"- Notes: {item['notes']}")
        lines.append('')
        lines.append('Top results:')
        for idx, row in enumerate(item.get('results') or [], start=1):
            lines.append(
                f"{idx}. **{row.get('title', '')}** | {row.get('section', '')} | relevance={float(row.get('relevance') or 0.0):.3f}"
            )
        lines.append('')

    return '\n'.join(lines).strip() + '\n'