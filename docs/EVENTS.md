# Kafka события

Топики:

- `copilot.audit.v1`
- `copilot.chat.v1`
- `copilot.suggest.v1`
- `copilot.tools.v1`

Пример `copilot.audit.v1`:

```json
{
  "trace_id": "req-003",
  "actor_role": "operator",
  "actor_id": "op-1",
  "conversation_id": "...",
  "case_id": "...",
  "event_type": "tool_result",
  "payload": {"tool":"create_case","result":{}}
}
```

