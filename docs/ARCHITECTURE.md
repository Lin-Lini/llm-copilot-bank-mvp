# Архитектура (кратко, но без сказок)

Система реализует human-in-the-loop copilot для оператора.

Компоненты:

- backend (FastAPI): Chat + Copilot Orchestrator + RAG + PII + Moderator + Case + Audit.
- worker: фоновые задачи `ANALYZE/RAG/DRAFT` по `task_id` (Redis queue).
- mcp-tools: отдельный сервис инструментов (тонкий слой, идемпотентность, строгая валидация).
- Postgres (pgvector): сообщения, кейсы, аудит, документы и векторы.
- Redis: кэш и статусы задач.
- MinIO: хранение документов.
- Kafka: event bus.

Ключевой принцип trust boundary:

- браузер недоверенный
- backend принимает actor context только из доверенного слоя через `X-Internal-Auth` + `X-Actor-*`.

