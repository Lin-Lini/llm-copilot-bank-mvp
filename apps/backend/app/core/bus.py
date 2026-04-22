from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from libs.common.config import settings
from libs.common.redis_client import get_redis


class Broadcast:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

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
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                with suppress(asyncio.QueueFull):
                    q.put_nowait(event)


@dataclass(slots=True, eq=False)
class RedisSubscription:
    topic: str
    queue: asyncio.Queue
    pubsub: Any
    reader_task: asyncio.Task

    async def get(self) -> dict[str, Any]:
        return await self.queue.get()


class RedisBroadcast:
    def __init__(self, *, prefix: str):
        self._prefix = prefix
        self._subs: set[RedisSubscription] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        async with self._lock:
            subs = list(self._subs)
            self._subs.clear()

        for sub in subs:
            await self._close_subscription(sub)

    def _channel(self, topic: str) -> str:
        return f'{self._prefix}:{topic}'

    async def _reader(self, pubsub, queue: asyncio.Queue) -> None:
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.01)
                    continue

                raw = msg.get('data')
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', errors='ignore')

                if isinstance(raw, str):
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        payload = {'type': 'malformed_event', 'raw': raw}
                elif isinstance(raw, dict):
                    payload = raw
                else:
                    payload = {'type': 'unknown_event', 'raw': raw}

                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    try:
                        _ = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(payload)
        except asyncio.CancelledError:
            raise
        finally:
            with suppress(Exception):
                await pubsub.close()

    async def subscribe(self, topic: str) -> RedisSubscription:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(self._channel(topic))
        reader_task = asyncio.create_task(self._reader(pubsub, q))
        sub = RedisSubscription(topic=topic, queue=q, pubsub=pubsub, reader_task=reader_task)

        async with self._lock:
            self._subs.add(sub)
        return sub

    async def _close_subscription(self, sub: RedisSubscription) -> None:
        sub.reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await sub.reader_task

        with suppress(Exception):
            await sub.pubsub.unsubscribe(self._channel(sub.topic))
        with suppress(Exception):
            await sub.pubsub.close()

    async def unsubscribe(self, topic: str, sub: RedisSubscription):
        async with self._lock:
            self._subs.discard(sub)
        await self._close_subscription(sub)

    async def publish(self, topic: str, event: dict[str, Any]):
        channel = self._channel(topic)
        payload = json.dumps(event, ensure_ascii=False)
        await get_redis().publish(channel, payload)


def build_chat_bus():
    backend = settings.chat_bus_backend.strip().lower()
    if backend == 'memory':
        return Broadcast()
    return RedisBroadcast(prefix=settings.chat_bus_prefix)


chat_bus = build_chat_bus()