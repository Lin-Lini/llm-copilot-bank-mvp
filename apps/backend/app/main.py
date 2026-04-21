from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apps.backend.app.api.v1.router import router as v1
from libs.common.db import init_db
from libs.common.kafka_bus import kafka_bus
from libs.common.observability import collect_backend_dependencies, summarize_readiness


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
    return {'ok': True, 'service': 'backend'}


@app.get('/readiness')
async def readiness():
    summary = summarize_readiness(await collect_backend_dependencies())
    if summary['ok']:
        return summary
    return JSONResponse(status_code=503, content=summary)