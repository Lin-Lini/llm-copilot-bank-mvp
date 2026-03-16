from __future__ import annotations

import asyncio

from fastapi import FastAPI

from libs.common.db import init_db
from libs.common.kafka_bus import kafka_bus
from apps.backend.app.api.v1.router import router as v1


app = FastAPI(title='LLM Copilot Backend', version='1.0')
app.include_router(v1)


@app.on_event('startup')
async def _startup():
    await init_db()
    await kafka_bus.start()


@app.on_event('shutdown')
async def _shutdown():
    await kafka_bus.stop()


@app.get('/health')
async def health():
    return {'ok': True}
