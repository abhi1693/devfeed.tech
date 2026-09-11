"""Source follows, prepared source recommendations and publication audiences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

app_version = "0.0.1"
revision = "0026_user_sources"
down_revision = "0025_user_theme_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_sources",
        sa.Column(
            "user_id", UUID, sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "source_id", UUID, sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_user_sources_source_user", "user_sources", ["source_id", "user_id"])
    op.create_table(
        "recommendation_source_events",
        sa.Column("source_id", UUID, primary_key=True),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("pass_version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("cursor", UUID),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column("user_recommendations", sa.Column("source_id", UUID))
    op.alter_column("user_recommendations", "topic_id", nullable=True)
    op.alter_column("user_recommendations", "seed_topic_id", nullable=True)
    op.add_column(
        "feed_notification_events",
        sa.Column("source_ids", ARRAY(UUID), nullable=False, server_default=sa.text("'{}'")),
    )
    op.execute("""
      CREATE TRIGGER recommendation_source_follows AFTER INSERT OR DELETE OR UPDATE ON user_sources
        FOR EACH ROW EXECUTE FUNCTION recommendation_request_user();
      CREATE FUNCTION recommendation_touch_source(target uuid) RETURNS void LANGUAGE sql AS $$
        INSERT INTO recommendation_source_events(source_id) VALUES(target)
        ON CONFLICT (source_id) DO UPDATE SET version = recommendation_source_events.version + 1;
      $$;
      CREATE FUNCTION recommendation_source_change() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE item record;
      BEGIN
        IF TG_TABLE_NAME = 'sources' THEN
          PERFORM recommendation_touch_source(NEW.id);
        ELSIF TG_TABLE_NAME = 'article_origins' THEN
          IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_source(OLD.source_id); END IF;
          IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_source(NEW.source_id); END IF;
        ELSE
          FOR item IN SELECT DISTINCT source_id FROM article_origins
            WHERE article_id = NEW.id ORDER BY source_id
          LOOP PERFORM recommendation_touch_source(item.source_id); END LOOP;
        END IF;
        RETURN NULL;
      END $$;
      CREATE TRIGGER recommendation_source_articles
        AFTER UPDATE OF publication_status, review_status, feed_at ON articles
        FOR EACH ROW WHEN ((OLD.publication_status, OLD.review_status, OLD.feed_at)
          IS DISTINCT FROM (NEW.publication_status, NEW.review_status, NEW.feed_at))
        EXECUTE FUNCTION recommendation_source_change();
      CREATE TRIGGER recommendation_source_origins
        AFTER INSERT OR UPDATE OR DELETE ON article_origins
        FOR EACH ROW EXECUTE FUNCTION recommendation_source_change();
      CREATE TRIGGER recommendation_source_approval AFTER UPDATE OF approval_status ON sources
        FOR EACH ROW WHEN (OLD.approval_status IS DISTINCT FROM NEW.approval_status)
        EXECUTE FUNCTION recommendation_source_change();
    """)


def downgrade():
    for name, table in [
        ("source_follows", "user_sources"),
        ("source_articles", "articles"),
        ("source_origins", "article_origins"),
        ("source_approval", "sources"),
    ]:
        op.execute(f"DROP TRIGGER recommendation_{name} ON {table}")
    op.execute("DROP FUNCTION recommendation_source_change()")
    op.execute("DROP FUNCTION recommendation_touch_source(uuid)")
    op.execute("DELETE FROM user_recommendations WHERE source_id IS NOT NULL")
    op.execute(
        "UPDATE user_recommendation_states SET invalidated = true, "
        "next_refresh_at = now(), dispatched_at = NULL"
    )
    op.drop_column("feed_notification_events", "source_ids")
    op.drop_column("user_recommendations", "source_id")
    op.alter_column("user_recommendations", "topic_id", nullable=False)
    op.alter_column("user_recommendations", "seed_topic_id", nullable=False)
    op.drop_table("recommendation_source_events")
    op.drop_table("user_sources")
