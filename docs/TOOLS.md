# Tools (MCP)

Инструменты MVP:

- create_case
- get_case_status
- get_transactions (mock)
- block_card (mock)
- unblock_card (mock)
- reissue_card (mock)
- get_card_limits (mock)
- set_card_limits (mock)
- toggle_online_payments (mock)

Требования:

- tool вызывается только после подтверждения оператора (`/copilot/tools/execute`)
- allowlist определяется policy-pack по intent/phase
- mcp-tools обеспечивает идемпотентность по `idempotency_key`

