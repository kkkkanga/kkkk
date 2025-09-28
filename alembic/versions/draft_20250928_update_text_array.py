"""
Draft Alembic migration: convert JSONB/text columns to PostgreSQL text[] where required.

THIS IS A DRAFT. Do NOT run this file as-is in production.

Purpose:
- Provide a safe, reviewable migration plan for converting `manage_memo` and
  `together_sites` (in `daily_sheet_rows`) to `text[]` when currently stored as
  JSONB or text.

Instructions:
1. Review the SQL in `upgrade()` and `downgrade()` below. Tailor to your actual
   database contents (data shapes) and test thoroughly on a copy of your prod DB.
2. Replace `revision` and `down_revision` with the correct values for your
   Alembic history before using `alembic upgrade`.
3. Optionally split the migration into two steps: (A) add new columns, (B)
   backfill data, (C) drop old columns and rename. That is safer for large tables.

Verification queries (examples):
  SELECT jsonb_typeof(manage_memo) as t, count(*) FROM daily_sheet_rows GROUP BY t;
  SELECT pg_typeof(manage_memo), count(*) FROM daily_sheet_rows GROUP BY pg_typeof(manage_memo);

"""
from alembic import op
import sqlalchemy as sa

# NOTE: set these to the correct revision identifiers for your repository
revision = 'draft_20250928_update_text_array'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Draft upgrade steps (commented SQL). Review and test before running.

    The commands below show two approaches:
    - In-place ALTER TYPE using a USING clause (works when you can express a
      conversion expression that handles current shapes).
    - Safer add-new-column -> backfill -> swap approach (recommended for large
      tables or unclear data shapes).
    """

    # -----------------------------
    # Option 1: In-place ALTER TYPE (risky for production; use only if tested)
    # -----------------------------
    # The SQL below attempts to convert JSONB arrays or text values to text[]:
    #
    # ALTER TABLE daily_sheet_rows
    #   ALTER COLUMN manage_memo TYPE text[] USING (
    #     CASE
    #       WHEN pg_typeof(manage_memo) = 'jsonb' AND jsonb_typeof(manage_memo) = 'array'
    #         THEN array(SELECT jsonb_array_elements_text(manage_memo))
    #       WHEN pg_typeof(manage_memo) = 'jsonb' THEN ARRAY[manage_memo::text]
    #       ELSE manage_memo::text[]  -- if it's already text[] this is no-op
    #     END
    #   );
    #
    # ALTER TABLE daily_sheet_rows
    #   ALTER COLUMN together_sites TYPE text[] USING (
    #     CASE
    #       WHEN pg_typeof(together_sites) = 'jsonb' AND jsonb_typeof(together_sites) = 'array'
    #         THEN array(SELECT jsonb_array_elements_text(together_sites))
    #       WHEN pg_typeof(together_sites) = 'jsonb' THEN ARRAY[together_sites::text]
    #       ELSE together_sites::text[]
    #     END
    #   );

    # -----------------------------
    # Option 2: Safer add-new-column -> backfill -> drop/rename
    # Recommended for production with large datasets.
    # -----------------------------
    # Example steps (commented):
    # 1) Add new columns
    # ALTER TABLE daily_sheet_rows ADD COLUMN manage_memo_new text[];
    # ALTER TABLE daily_sheet_rows ADD COLUMN together_sites_new text[];
    #
    # 2) Backfill with conversion logic (example for manage_memo)
    # UPDATE daily_sheet_rows SET manage_memo_new = (
    #   CASE
    #     WHEN pg_typeof(manage_memo) = 'jsonb' AND jsonb_typeof(manage_memo) = 'array'
    #       THEN array(SELECT jsonb_array_elements_text(manage_memo))
    #     WHEN pg_typeof(manage_memo) = 'jsonb' THEN ARRAY[manage_memo::text]
    #     WHEN pg_typeof(manage_memo) = 'text' THEN ARRAY[manage_memo]
    #     ELSE NULL
    #   END
    # );
    #
    # 3) Validate counts and spot-check rows
    # 4) Drop old columns and rename
    # ALTER TABLE daily_sheet_rows DROP COLUMN manage_memo;
    # ALTER TABLE daily_sheet_rows RENAME COLUMN manage_memo_new TO manage_memo;

    # NOTE: This file intentionally does not execute database schema changes.
    # Use it as a reviewed template and paste tested SQL into a real migration file
    # with the proper Alembic `revision` / `down_revision` values.


def downgrade():
    """Draft downgrade guidance — mirrors the upgrade but in reverse.

    Implementing a true downgrade depends on how upgrade was performed. If the
    upgrade dropped or renamed columns, you must recreate the previous shape and
    convert arrays back to the previous representation.
    """

    # Example (commented):
    # ALTER TABLE daily_sheet_rows ADD COLUMN manage_memo_old jsonb;
    # UPDATE daily_sheet_rows SET manage_memo_old = to_jsonb(manage_memo);
    # ALTER TABLE daily_sheet_rows DROP COLUMN manage_memo;
    # ALTER TABLE daily_sheet_rows RENAME COLUMN manage_memo_old TO manage_memo;

    pass
