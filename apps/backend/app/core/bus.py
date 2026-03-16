from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class Broadcast:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subs[topic].add(q)
        return q

    async def unsubscribe(self, topic: str, q: asyncio.Queue):
        async with self._lock:
            bucket = self._subs.get(topic)
            if not bucket:
                return
            bucket.discard(q)
            if not bucket:
                self._subs.pop(topic, None)

    async def publish(self, topic: str, event: dict[str, Any]):
        async with self._lock:
            subs = list(self._subs.get(topic, set()))
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


chat_bus = Broadcast()
