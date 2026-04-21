from __future__ import annotations

from alembic import op


revision = '20260421_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_actor_role VARCHAR(32)")
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_actor_id VARCHAR(128)")

    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_type VARCHAR(64) DEFAULT 'Unknown'")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS priority VARCHAR(32) DEFAULT 'medium'")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS sla_deadline VARCHAR(64)")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS customer_ref_masked VARCHAR(128) DEFAULT ''")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS card_ref_masked VARCHAR(128) DEFAULT ''")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS operation_ref VARCHAR(128) DEFAULT ''")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS dispute_reason VARCHAR(256) DEFAULT ''")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS facts_confirmed_json TEXT DEFAULT '[]'")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS facts_pending_json TEXT DEFAULT '[]'")
    op.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS decision_summary TEXT DEFAULT ''")

    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_code VARCHAR(64) DEFAULT ''")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS version_label VARCHAR(32) DEFAULT '1.0'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_date VARCHAR(32) DEFAULT ''")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) DEFAULT 'procedure'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_priority DOUBLE PRECISION DEFAULT 1.0")

    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS doc_code VARCHAR(64) DEFAULT ''")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS version_label VARCHAR(32) DEFAULT '1.0'")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS effective_date VARCHAR(32) DEFAULT ''")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) DEFAULT 'procedure'")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS source_priority DOUBLE PRECISION DEFAULT 1.0")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS section_path VARCHAR(512) DEFAULT ''")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS chunk_type VARCHAR(32) DEFAULT 'paragraph'")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS risk_tags VARCHAR(256) DEFAULT ''")
    op.execute("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS is_mandatory_step BOOLEAN DEFAULT FALSE")

    op.execute("ALTER TABLE case_timeline ADD COLUMN IF NOT EXISTS payload_json JSONB")

    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS payload_json JSONB")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS retrieval_snapshot_json JSONB")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS state_before_json JSONB")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS state_after_json JSONB")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS cache_info_json JSONB")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS prompt_hash VARCHAR(64)")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32)")

    op.execute("""
        UPDATE audit_events
        SET payload_json = CASE
            WHEN payload IS NULL OR btrim(payload) = '' THEN '{}'::jsonb
            ELSE payload::jsonb
        END
        WHERE payload_json IS NULL
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_doc_code_effective_date ON documents (doc_code, effective_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rag_chunks_doc_code_effective_date ON rag_chunks (doc_code, effective_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rag_chunks_source_type_chunk_type ON rag_chunks (source_type, chunk_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rag_chunks_mandatory ON rag_chunks (is_mandatory_step)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_prompt_hash ON audit_events (prompt_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_policy_version ON audit_events (policy_version)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_events_policy_version")
    op.execute("DROP INDEX IF EXISTS ix_audit_events_prompt_hash")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_mandatory")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_source_type_chunk_type")
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_doc_code_effective_date")
    op.execute("DROP INDEX IF EXISTS ix_documents_doc_code_effective_date")

    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS policy_version")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS prompt_hash")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS cache_info_json")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS state_after_json")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS state_before_json")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS retrieval_snapshot_json")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS payload_json")

    op.execute("ALTER TABLE case_timeline DROP COLUMN IF EXISTS payload_json")