from __future__ import annotations

from alembic import op


revision = '20260422_0003'
down_revision = '20260422_0002'
branch_labels = None
depends_on = None


def _ensure_jsonb_list_column(table_name: str, column_name: str) -> None:
    op.execute(
        f"""
DO $$
DECLARE
    col_type text;
BEGIN
    SELECT data_type
      INTO col_type
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = '{table_name}'
       AND column_name = '{column_name}';

    IF col_type IS NULL THEN
        EXECUTE '
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} JSONB NOT NULL DEFAULT ''[]''::jsonb
        ';
    ELSIF col_type <> 'jsonb' THEN
        EXECUTE '
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name} DROP DEFAULT,
            ALTER COLUMN {column_name} TYPE JSONB
            USING CASE
                WHEN {column_name} IS NULL OR btrim({column_name}) = '''' THEN ''[]''::jsonb
                ELSE {column_name}::jsonb
            END,
            ALTER COLUMN {column_name} SET DEFAULT ''[]''::jsonb
        ';
    END IF;

    EXECUTE '
        UPDATE {table_name}
           SET {column_name} = ''[]''::jsonb
         WHERE {column_name} IS NULL
    ';

    EXECUTE '
        ALTER TABLE {table_name}
        ALTER COLUMN {column_name} SET NOT NULL
    ';
END $$;
"""
    )


def upgrade() -> None:
    _ensure_jsonb_list_column('cases', 'facts_confirmed_json')
    _ensure_jsonb_list_column('cases', 'facts_pending_json')


def downgrade() -> None:
    op.execute(
        """
ALTER TABLE cases
ALTER COLUMN facts_confirmed_json DROP DEFAULT,
ALTER COLUMN facts_confirmed_json TYPE TEXT
USING COALESCE(facts_confirmed_json::text, '[]'),
ALTER COLUMN facts_confirmed_json SET DEFAULT '[]'
"""
    )
    op.execute(
        """
ALTER TABLE cases
ALTER COLUMN facts_pending_json DROP DEFAULT,
ALTER COLUMN facts_pending_json TYPE TEXT
USING COALESCE(facts_pending_json::text, '[]'),
ALTER COLUMN facts_pending_json SET DEFAULT '[]'
"""
    )