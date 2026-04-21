from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text

from libs.common.config import settings
from libs.common.db import SessionLocal
from libs.common.redis_client import get_redis


def _status(ok: bool, *, detail: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {'ok': ok}
    if detail is not None:
        out['detail'] = detail
    if extra:
        out.update(extra)
    return out


async def check_postgres() -> dict[str, Any]:
    try:
        async with SessionLocal() as db:
            await db.execute(text('SELECT 1'))
        return _status(True, detail='postgres reachable')
    except Exception as e:
        return _status(False, detail=f'postgres error: {e}')


async def check_redis() -> dict[str, Any]:
    try:
        r = get_redis()
        pong = await r.ping()
        return _status(bool(pong), detail='redis ping ok' if pong else 'redis ping failed')
    except Exception as e:
        return _status(False, detail=f'redis error: {e}')


async def check_minio() -> dict[str, Any]:
    from minio import Minio

    def _probe() -> dict[str, Any]:
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        bucket_exists = client.bucket_exists(settings.minio_bucket)
        return _status(
            True,
            detail='minio reachable',
            extra={'bucket_exists': bucket_exists, 'bucket': settings.minio_bucket},
        )

    try:
        return await asyncio.to_thread(_probe)
    except Exception as e:
        return _status(False, detail=f'minio error: {e}', extra={'bucket': settings.minio_bucket})


async def check_kafka() -> dict[str, Any]:
    host, _, port_raw = settings.kafka_bootstrap.partition(':')
    port = int(port_raw or 9092)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
        writer.close()
        await writer.wait_closed()
        return _status(True, detail='kafka tcp probe ok', extra={'bootstrap': settings.kafka_bootstrap})
    except Exception as e:
        return _status(False, detail=f'kafka error: {e}', extra={'bootstrap': settings.kafka_bootstrap})


async def collect_backend_dependencies() -> dict[str, dict[str, Any]]:
    pg, rd, mn, kf = await asyncio.gather(
        check_postgres(),
        check_redis(),
        check_minio(),
        check_kafka(),
    )
    return {
        'postgres': pg,
        'redis': rd,
        'minio': mn,
        'kafka': kf,
    }


async def collect_mcp_dependencies() -> dict[str, dict[str, Any]]:
    rd, kf = await asyncio.gather(
        check_redis(),
        check_kafka(),
    )
    return {
        'redis': rd,
        'kafka': kf,
    }


def summarize_readiness(components: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ok = all(bool(component.get('ok')) for component in components.values())
    return {
        'ok': ok,
        'components': components,
    }