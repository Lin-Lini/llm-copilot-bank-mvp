import pytest
from fastapi import HTTPException

from contracts.schemas import ToolExecuteRequest, ToolName
from apps.mcp_tools.app.api.v1.routes import tools


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value


@pytest.mark.asyncio
async def test_same_idempotency_key_and_same_params_returns_cached_result(monkeypatch):
    fake_redis = FakeRedis()

    async def _noop_publish(*args, **kwargs):
        return None

    monkeypatch.setattr(tools, 'get_redis', lambda: fake_redis)
    monkeypatch.setattr(tools.kafka_bus, 'publish', _noop_publish)

    actor = {'role': 'operator', 'id': 'op-1'}

    req = ToolExecuteRequest(
        tool=ToolName.block_card,
        params={'conversation_id': 'conv-1', 'reason': 'fraud'},
        idempotency_key='idem-1',
        actor_role='operator',
        actor_id='op-1',
        trace_id='trace-1',
    )

    resp1 = await tools.execute(req, actor=actor, x_request_id='req-1')
    resp2 = await tools.execute(req, actor=actor, x_request_id='req-2')

    assert resp1.result == resp2.result


@pytest.mark.asyncio
async def test_same_idempotency_key_and_different_params_returns_409(monkeypatch):
    fake_redis = FakeRedis()

    async def _noop_publish(*args, **kwargs):
        return None

    monkeypatch.setattr(tools, 'get_redis', lambda: fake_redis)
    monkeypatch.setattr(tools.kafka_bus, 'publish', _noop_publish)

    actor = {'role': 'operator', 'id': 'op-1'}

    req1 = ToolExecuteRequest(
        tool=ToolName.block_card,
        params={'conversation_id': 'conv-1', 'reason': 'fraud'},
        idempotency_key='idem-2',
        actor_role='operator',
        actor_id='op-1',
        trace_id='trace-2',
    )

    req2 = ToolExecuteRequest(
        tool=ToolName.block_card,
        params={'conversation_id': 'conv-1', 'reason': 'lost_card'},
        idempotency_key='idem-2',
        actor_role='operator',
        actor_id='op-1',
        trace_id='trace-3',
    )

    await tools.execute(req1, actor=actor, x_request_id='req-1')

    with pytest.raises(HTTPException) as exc:
        await tools.execute(req2, actor=actor, x_request_id='req-2')

    assert exc.value.status_code == 409
    assert 'idempotency_key reused with different params' in str(exc.value.detail)