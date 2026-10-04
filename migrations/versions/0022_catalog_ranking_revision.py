"""Include topic description vocabulary in the authoritative ranking revision."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

IDENTITY_COLUMNS = "id, name, slug, aliases, kind, keywords, status"


def replace_revision_function(columns):
    op.execute(f"""
        CREATE OR REPLACE FUNCTION refresh_topics_revision() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NOT EXISTS (SELECT 1 FROM new_rows) THEN RETURN NULL; END IF;
          ELSIF TG_OP = 'DELETE' THEN
            IF NOT EXISTS (SELECT 1 FROM old_rows) THEN RETURN NULL; END IF;
          ELSIF TG_OP = 'UPDATE' THEN
            IF NOT EXISTS (
              SELECT {columns} FROM new_rows EXCEPT SELECT {columns} FROM old_rows
            ) THEN RETURN NULL; END IF;
          END IF;
          INSERT INTO catalog_revisions(name) VALUES (TG_TABLE_NAME)
          ON CONFLICT (name) DO UPDATE SET revision = gen_random_uuid();
          RETURN NULL;
        END $fn$
    """)
    # Evict snapshots cached before the trigger covered all ranking inputs.
    op.execute("UPDATE catalog_revisions SET revision=gen_random_uuid() WHERE name='topics'")


def upgrade():
    replace_revision_function(IDENTITY_COLUMNS + ", description, ai_description")


def downgrade():
    replace_revision_function(IDENTITY_COLUMNS)
