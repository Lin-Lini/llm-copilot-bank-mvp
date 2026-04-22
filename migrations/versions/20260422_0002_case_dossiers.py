from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '20260422_0002'
down_revision = '20260421_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'case_dossiers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('schema_version', sa.String(length=16), nullable=False, server_default='1.0'),
        sa.Column('current_status', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('built_from_timeline_event_id', sa.Integer(), nullable=True),
        sa.Column('payload_json', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )
    op.create_index('ix_case_dossiers_case_id', 'case_dossiers', ['case_id'], unique=True, if_not_exists=True)
    op.create_index(
        'ix_case_dossiers_status_timeline',
        'case_dossiers',
        ['current_status', 'built_from_timeline_event_id'],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('ix_case_dossiers_status_timeline', table_name='case_dossiers', if_exists=True)
    op.drop_index('ix_case_dossiers_case_id', table_name='case_dossiers', if_exists=True)
    op.drop_table('case_dossiers', if_exists=True)