from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from typing import Any

from aiokafka import AIOKafkaProducer

from libs.common.config import settings

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

            retries = 12
            base_delay = 0.5
            max_delay = 5.0

            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                prod: AIOKafkaProducer | None = None
                try:
                    prod = AIOKafkaProducer(
                        bootstrap_servers=settings.kafka_bootstrap,
                    )
                    await prod.start()
                    self._producer = prod
                    return
                except Exception as e:
                    last_exc = e
                    if prod is not None:
                        with contextlib.suppress(Exception):
                            await prod.stop()
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay *= 0.9 + random.random() * 0.2
                    log.warning("Kafka start failed (attempt %s/%s): %s", attempt, retries, e)
                    await asyncio.sleep(delay)

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

        try:
            await self._producer.send_and_wait(topic, data)
            return
        except Exception as e:
            log.warning("Kafka publish failed for topic %s: %s", topic, e)

        await self.stop()
        await asyncio.sleep(1.0)
        await self.start()
        if self._producer is None:
            return

        try:
            await self._producer.send_and_wait(topic, data)
        except Exception as e:
            log.warning("Kafka publish retry failed for topic %s: %s", topic, e)


kafka_bus = KafkaBus()