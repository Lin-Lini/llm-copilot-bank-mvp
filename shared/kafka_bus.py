from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

from aiokafka import AIOKafkaProducer

from shared.config import settings

log = logging.getLogger("kafka_bus")


class KafkaBus:
    def __init__(self):
        self._producer: AIOKafkaProducer | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        if not settings.kafka_enabled:
            return

        async with self._lock:
            if self._producer is not None:
                return

            # Kafka in docker-compose often reports "started" before it's actually ready.
            # We retry so the whole stack doesn't die on boot.
            retries = 12
            base_delay = 0.5
            max_delay = 5.0

            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    prod = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
                    await prod.start()
                    self._producer = prod
                    return
                except Exception as e:
                    last_exc = e
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay *= 0.9 + random.random() * 0.2  # light jitter
                    log.warning("Kafka start failed (attempt %s/%s): %s", attempt, retries, e)
                    await asyncio.sleep(delay)

            # Don't kill the service startup if Kafka is down.
            log.error("Kafka is unavailable; continuing without event bus. Last error: %s", last_exc)

    async def stop(self):
        if self._producer is not None:
            try:
                await self._producer.stop()
            finally:
                self._producer = None

    async def publish(self, topic: str, payload: dict[str, Any]):
        if not settings.kafka_enabled:
            return
        if self._producer is None:
            await self.start()
        if self._producer is None:
            return

        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await self._producer.send_and_wait(topic, data)


kafka_bus = KafkaBus()
