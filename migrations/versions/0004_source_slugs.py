"""Stable, indexed source URLs with legacy UUID lookup retained."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sources", sa.Column("slug", sa.String(200), nullable=True))
    op.create_unique_constraint("uq_sources_slug", "sources", ["slug"])
    op.execute(r"""
    CREATE FUNCTION new_source_slug(source_name text) RETURNS text
    LANGUAGE plpgsql AS $$
    DECLARE base text; candidate text; suffix bigint := 1;
    BEGIN
      -- Source creation is infrequent. Serialize allocation across all bases,
      -- including collisions such as 'Example' + 'Example-2'.
      PERFORM pg_advisory_xact_lock(749126380412);
      base := coalesce(nullif(trim(both '-' from left(regexp_replace(
        regexp_replace(lower(normalize(source_name, NFKD)), U&'[\0300-\036f]', '', 'g'),
        '[^a-z0-9]+', '-', 'g'), 170)), ''), 'source');
      IF base = 'suggest' OR base ~ '^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$' THEN
        base := 'source-' || base;
      END IF;
      candidate := base;
      WHILE EXISTS (SELECT 1 FROM sources WHERE slug = candidate) LOOP
        suffix := suffix + 1;
        candidate := base || '-' || suffix::text;
      END LOOP;
      RETURN candidate;
    END $$
    """)
    op.execute("""
    DO $$ DECLARE source record; BEGIN
      FOR source IN SELECT id, name FROM sources ORDER BY created_at, id LOOP
        UPDATE sources SET slug = new_source_slug(source.name) WHERE id = source.id;
      END LOOP;
    END $$
    """)
    op.alter_column("sources", "slug", nullable=False)
    op.create_check_constraint(
        "ck_sources_slug",
        "sources",
        "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND slug <> 'suggest' "
        "AND slug !~ '^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$'",
    )
    op.execute("""
    CREATE FUNCTION assign_source_slug() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'INSERT' THEN
        NEW.slug := new_source_slug(NEW.name);
      ELSIF NEW.slug IS DISTINCT FROM OLD.slug THEN
        RAISE EXCEPTION 'Source slug is immutable' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute("""CREATE TRIGGER sources_assign_slug BEFORE INSERT OR UPDATE OF slug
      ON sources FOR EACH ROW EXECUTE FUNCTION assign_source_slug()""")
    # Source documents gain searchable slugs. UUID identities remain unchanged.
    op.execute("INSERT INTO search_events(kind, entity_id) SELECT 'sources', id FROM sources")


def downgrade():
    op.execute("DROP TRIGGER sources_assign_slug ON sources")
    op.execute("DROP FUNCTION assign_source_slug()")
    op.execute("DROP FUNCTION new_source_slug(text)")
    op.drop_constraint("ck_sources_slug", "sources", type_="check")
    op.drop_constraint("uq_sources_slug", "sources", type_="unique")
    op.drop_column("sources", "slug")
