from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from libs.common.db import SessionLocal, init_db
from libs.common.rag_eval import aggregate_reports, evaluate_entry, render_markdown_report
from libs.common.rag_search import hybrid_search


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Run RAG evaluation on the goldset.')
    p.add_argument('--goldset', default='docs/eval/rag_goldset.json', help='Path to goldset JSON file')
    p.add_argument('--output-json', default='docs/eval/rag_eval_report.json', help='Path to output JSON report')
    p.add_argument('--output-md', default='docs/eval/rag_eval_report.md', help='Path to output Markdown report')
    return p.parse_args()


async def _run(goldset_path: Path, output_json: Path, output_md: Path) -> None:
    await init_db()

    goldset = json.loads(goldset_path.read_text(encoding='utf-8'))
    if not isinstance(goldset, list):
        raise RuntimeError('Goldset file must contain a JSON list')

    reports: list[dict] = []

    async with SessionLocal() as db:
        for entry in goldset:
            query = str(entry.get('query') or '').strip()
            top_k = int(entry.get('top_k') or 5)
            results = await hybrid_search(db, query, top_k=top_k)
            reports.append(evaluate_entry(entry, results))

    summary = aggregate_reports(reports)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(
        json.dumps(
            {
                'summary': summary,
                'items': reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    output_md.write_text(render_markdown_report(summary, reports), encoding='utf-8')

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'JSON report: {output_json}')
    print(f'Markdown report: {output_md}')


def main() -> None:
    args = parse_args()
    asyncio.run(
        _run(
            goldset_path=Path(args.goldset),
            output_json=Path(args.output_json),
            output_md=Path(args.output_md),
        )
    )


if __name__ == '__main__':
    main()