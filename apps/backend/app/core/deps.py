from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.db import SessionLocal


async def get_db() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
