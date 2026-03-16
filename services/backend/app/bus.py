from __future__ import annotations

import asyncio
from typing import Any


class Broadcast:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subs.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            self._subs.discard(q)

    async def publish(self, event: dict[str, Any]):
        async with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # промахнулись, как обычно бывает с буферами
                pass


chat_bus = Broadcast()
