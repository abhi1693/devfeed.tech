"""Stable title-based public article slugs, including existing articles."""

import sqlalchemy as sa
from alembic import op

app_version = "0.0.1"
revision = "0024_article_slugs"
down_revision = "0023_user_feed_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SEQUENCE article_slug_number")
    op.add_column("articles", sa.Column("slug", sa.String(200), nullable=True))
    # A globally allocated suffix avoids collision probes and races during bulk
    # ingestion. Normalized titles cannot contain a second numeric suffix with
    # the same allocated number. Slugs are assigned once, never on title edits.
    op.execute(r"""
        CREATE FUNCTION new_article_slug(article_title text) RETURNS text
        LANGUAGE sql VOLATILE AS $$
            SELECT coalesce(nullif(trim(both '-' from left(
                regexp_replace(
                    regexp_replace(lower(normalize(article_title, NFKD)),
                                   U&'[\0300-\036f]', '', 'g'),
                    '[^a-z0-9]+', '-', 'g'), 170)), ''), 'article')
                || '-' || nextval('article_slug_number')::text
        $$;
        CREATE FUNCTION assign_article_slug() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.slug := new_article_slug(NEW.title);
            RETURN NEW;
        END $$;
        CREATE TRIGGER articles_assign_slug BEFORE INSERT ON articles
            FOR EACH ROW EXECUTE FUNCTION assign_article_slug();
    """)
    op.execute("UPDATE articles SET slug = new_article_slug(title)")
    op.alter_column("articles", "slug", nullable=False)
    op.create_unique_constraint("uq_articles_slug", "articles", ["slug"])


def downgrade():
    op.execute("DROP TRIGGER articles_assign_slug ON articles")
    op.execute("DROP FUNCTION assign_article_slug()")
    op.drop_column("articles", "slug")
    op.execute("DROP FUNCTION new_article_slug(text)")
    op.execute("DROP SEQUENCE article_slug_number")
