from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from libs.common.config import settings


revision = '20260421_0001'
down_revision = None
branch_labels = None
depends_on = None


def _create_base_tables_if_missing() -> None:
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('owner_actor_role', sa.String(length=32), nullable=True),
        sa.Column('owner_actor_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_conversations_owner_actor_role', 'conversations', ['owner_actor_role'], if_not_exists=True)
    op.create_index('ix_conversations_owner_actor_id', 'conversations', ['owner_actor_id'], if_not_exists=True)

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('conversation_id', sa.String(length=36), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('actor_role', sa.String(length=32), nullable=False),
        sa.Column('actor_id', sa.String(length=128), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'], if_not_exists=True)

    op.create_table(
        'cases',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('case_type', sa.String(length=64), nullable=False, server_default='Unknown'),
        sa.Column('priority', sa.String(length=32), nullable=False, server_default='medium'),
        sa.Column('sla_deadline', sa.String(length=64), nullable=True),
        sa.Column('customer_ref_masked', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('card_ref_masked', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('operation_ref', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('dispute_reason', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('facts_confirmed_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('facts_pending_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('decision_summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('summary_public', sa.Text(), nullable=False, server_default=''),
        sa.Column('notes', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_cases_conversation_id', 'cases', ['conversation_id'], if_not_exists=True)

    op.create_table(
        'case_timeline',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('kind', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('payload_json', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_case_timeline_case_id', 'case_timeline', ['case_id'], if_not_exists=True)

    op.create_table(
        'case_profile_fields',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('field_name', sa.String(length=128), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('trace_id', sa.String(length=64), nullable=False),
        sa.Column('confirmed_by', sa.String(length=128), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_case_profile_fields_case_id', 'case_profile_fields', ['case_id'], if_not_exists=True)

    op.create_table(
        'documents',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('storage_key', sa.String(length=512), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('doc_code', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('version_label', sa.String(length=32), nullable=False, server_default='1.0'),
        sa.Column('effective_date', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('source_type', sa.String(length=32), nullable=False, server_default='procedure'),
        sa.Column('source_priority', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_documents_source_type', 'documents', ['source_type'], if_not_exists=True)

    op.create_table(
        'rag_chunks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('doc_code', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('version_label', sa.String(length=32), nullable=False, server_default='1.0'),
        sa.Column('effective_date', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('source_type', sa.String(length=32), nullable=False, server_default='procedure'),
        sa.Column('source_priority', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('section', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('section_path', sa.String(length=512), nullable=False, server_default=''),
        sa.Column('chunk_type', sa.String(length=32), nullable=False, server_default='paragraph'),
        sa.Column('risk_tags', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('is_mandatory_step', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(settings.rag_dim), nullable=True),
        if_not_exists=True,
    )
    op.create_index('ix_rag_chunks_doc_id', 'rag_chunks', ['doc_id'], if_not_exists=True)
    op.create_index('ix_rag_chunks_doc_code', 'rag_chunks', ['doc_code'], if_not_exists=True)
    op.create_index('ix_rag_chunks_effective_date', 'rag_chunks', ['effective_date'], if_not_exists=True)
    op.create_index('ix_rag_chunks_source_type', 'rag_chunks', ['source_type'], if_not_exists=True)
    op.create_index('ix_rag_chunks_chunk_type', 'rag_chunks', ['chunk_type'], if_not_exists=True)
    op.create_index('ix_rag_chunks_is_mandatory_step', 'rag_chunks', ['is_mandatory_step'], if_not_exists=True)

    op.create_table(
        'audit_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('trace_id', sa.String(length=64), nullable=False),
        sa.Column('actor_role', sa.String(length=32), nullable=False),
        sa.Column('actor_id', sa.String(length=128), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('case_id', sa.String(length=36), nullable=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('payload_json', JSONB, nullable=True),
        sa.Column('retrieval_snapshot_json', JSONB, nullable=True),
        sa.Column('state_before_json', JSONB, nullable=True),
        sa.Column('state_after_json', JSONB, nullable=True),
        sa.Column('cache_info_json', JSONB, nullable=True),
        sa.Column('prompt_hash', sa.String(length=64), nullable=True),
        sa.Column('policy_version', sa.String(length=32), nullable=True),
        if_not_exists=True,
    )
    op.create_index('ix_audit_events_trace_id', 'audit_events', ['trace_id'], if_not_exists=True)
    op.create_index('ix_audit_events_conversation_id', 'audit_events', ['conversation_id'], if_not_exists=True)
    op.create_index('ix_audit_events_case_id', 'audit_events', ['case_id'], if_not_exists=True)
    op.create_index('ix_audit_events_prompt_hash', 'audit_events', ['prompt_hash'], if_not_exists=True)
    op.create_index('ix_audit_events_policy_version', 'audit_events', ['policy_version'], if_not_exists=True)


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    _create_base_tables_if_missing()

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

    op.execute(
        '''
        UPDATE audit_events
        SET payload_json = CASE
            WHEN payload IS NULL OR btrim(payload) = '' THEN '{}'::jsonb
            ELSE payload::jsonb
        END
        WHERE payload_json IS NULL
        '''
    )

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

    op.drop_table('audit_events', if_exists=True)
    op.drop_table('rag_chunks', if_exists=True)
    op.drop_table('documents', if_exists=True)
    op.drop_table('case_profile_fields', if_exists=True)
    op.drop_table('case_timeline', if_exists=True)
    op.drop_table('cases', if_exists=True)
    op.drop_table('messages', if_exists=True)
    op.drop_table('conversations', if_exists=True)