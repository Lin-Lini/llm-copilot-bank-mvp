from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from libs.common.config import settings


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _patch_schema(conn):
    stmts = [
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_actor_role VARCHAR(32)",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_actor_id VARCHAR(128)",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_type VARCHAR(64) DEFAULT 'Unknown'",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS priority VARCHAR(32) DEFAULT 'medium'",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS sla_deadline VARCHAR(64)",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS customer_ref_masked VARCHAR(128) DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS card_ref_masked VARCHAR(128) DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS operation_ref VARCHAR(128) DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS dispute_reason VARCHAR(256) DEFAULT ''",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS facts_confirmed_json TEXT DEFAULT '[]'",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS facts_pending_json TEXT DEFAULT '[]'",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS decision_summary TEXT DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_code VARCHAR(64) DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS version_label VARCHAR(32) DEFAULT '1.0'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_date VARCHAR(32) DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) DEFAULT 'procedure'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_priority DOUBLE PRECISION DEFAULT 1.0",
        "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS doc_code VARCHAR(64) DEFAULT ''",
        "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) DEFAULT 'procedure'",
        "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS source_priority DOUBLE PRECISION DEFAULT 1.0",
    ]
    for stmt in stmts:
        await conn.execute(text(stmt))


async def init_db():
    from libs.common.models import Base

    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        await conn.run_sync(Base.metadata.create_all)
        await _patch_schema(conn)
