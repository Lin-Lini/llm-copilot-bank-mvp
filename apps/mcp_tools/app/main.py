from __future__ import annotations

from fastapi import FastAPI

from libs.common.kafka_bus import kafka_bus
from apps.mcp_tools.app.api.v1.router import router as v1


app = FastAPI(title='MCP Tools Server', version='1.0')
app.include_router(v1)


@app.on_event('startup')
async def _startup():
    await kafka_bus.start()


@app.on_event('shutdown')
async def _shutdown():
    await kafka_bus.stop()


@app.get('/health')
async def health():
    return {'ok': True}
