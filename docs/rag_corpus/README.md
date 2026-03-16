# Seed RAG corpus

Готовый стартовый корпус для банковского copilot-сценария.

Состав:
- policy / procedure: REG-DSP-001, REG-BLK-002, REG-TRX-004, REG-LST-005, REG-SUB-006, REG-ESC-007, REG-STS-008, REG-FBK-009
- security: REG-SEC-003
- scripts: SCRIPT-OPS-001, SCRIPT-OPS-002

Идея простая: policy/security документы должны побеждать при retrieval, а scripts помогают DRAFT-слою формулировать ответ, но не подменяют регламенты.

Загрузка в проект:
1. POST `/api/v1/docs/bootstrap-seed`
2. при необходимости повторный POST `/api/v1/docs/reindex`

Ретривер внутри проекта:
- вырезает служебную шапку из индекса;
- сохраняет `doc_code`, `source_type`, `source_priority`;
- поднимает policy/security выше script;
- ограничивает дубли из одного и того же документа.
