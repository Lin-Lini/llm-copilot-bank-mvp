import asyncio

import pytest

from apps.backend.app.core.bus import Broadcast


@pytest.mark.asyncio
async def test_broadcast_isolates_topics():
    bus = Broadcast()
    q1 = await bus.subscribe('conv-1')
    q2 = await bus.subscribe('conv-2')

    await bus.publish('conv-1', {'msg': 1})

    got1 = await asyncio.wait_for(q1.get(), timeout=0.2)
    assert got1 == {'msg': 1}

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q2.get(), timeout=0.05)

    await bus.unsubscribe('conv-1', q1)
    await bus.unsubscribe('conv-2', q2)
