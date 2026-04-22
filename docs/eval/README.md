# RAG Evaluation

В этой директории хранятся артефакты оценки retrieval-качества.

## Состав

- `rag_goldset.json` — набор контрольных запросов и ожидаемых релевантных документов
- `rag_eval_report.json` — машинно-читаемый отчет
- `rag_eval_report.md` — человеко-читаемый отчет

## Формат goldset

Каждый объект в `rag_goldset.json` содержит:

- `name` — имя сценария
- `query` — поисковый запрос
- `top_k` — глубина поиска
- `expected_any` — список допустимых релевантных ориентиров
- `expected_security_any` — список обязательных security-ориентиров для security-sensitive запросов
- `notes` — комментарий

Элемент `expected_any` и `expected_security_any` поддерживает поля:
- `title_contains`
- `section_contains`
- `quote_contains`

Совпадение считается релевантным, если все непустые условия в одном target выполнены.

## Как запустить

```bash
PYTHONPATH=packages/contracts/src:. python scripts/eval_rag.py
```

## Метрики

Скрипт считает:

- `Recall@k`
- `MRR`
- `nDCG@k`
- `pass_rate`
- `security_coverage_at_k`
- `security_pass_rate`

## Назначение

Эти артефакты нужны для того, чтобы проверять retrieval не «на глаз», а по воспроизводимому набору сценариев, включая отдельный контроль security coverage.
