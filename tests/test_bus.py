import asyncio

import pytest

from apps.backend.app.core.bus import Broadcast, RedisSubscription


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


def test_redis_subscription_is_hashable_for_set_storage():
    sub = RedisSubscription(
        topic='conv-1',
        queue=asyncio.Queue(),
        pubsub=object(),
        reader_task=object(),
    )

    bucket = {sub}
    assert sub in bucket