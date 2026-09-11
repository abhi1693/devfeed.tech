"""Precomputed user/article edges and transactional recommendation work."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

app_version = "0.0.1"
revision = "0020_user_recommendations"
down_revision = "0019_user_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_recommendation_states",
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("generation", UUID),
        sa.Column("invalidated", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "next_refresh_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("interest_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_user_recommendations_due", "user_recommendation_states", ["next_refresh_at"]
    )
    op.create_table(
        "user_interests",
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("topic_id", UUID, primary_key=True),
        sa.Column("seed_topic_id", UUID, nullable=False),
        sa.Column("weight", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
    )
    op.create_index("ix_user_interests_topic_user", "user_interests", ["topic_id", "user_id"])
    op.create_table(
        "user_recommendations",
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            UUID,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("topic_id", UUID, nullable=False),
        sa.Column("seed_topic_id", UUID, nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.UniqueConstraint("user_id", "position"),
    )
    op.create_table(
        "recommendation_topic_events",
        sa.Column("topic_id", UUID, primary_key=True),
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
    op.execute("""
        CREATE FUNCTION recommendation_request_user() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid;
        BEGIN
          IF TG_TABLE_NAME = 'user_accounts' THEN target := NEW.id;
          ELSIF TG_OP = 'DELETE' THEN target := OLD.user_id;
          ELSE target := NEW.user_id; END IF;
          INSERT INTO user_recommendation_states(user_id)
            SELECT id FROM user_accounts WHERE id = target
          ON CONFLICT (user_id) DO UPDATE SET invalidated = true,
            next_refresh_at = now(), dispatched_at = NULL, attempts = 0;
          RETURN NULL;
        END $$;
        CREATE TRIGGER recommendation_account AFTER INSERT ON user_accounts
          FOR EACH ROW EXECUTE FUNCTION recommendation_request_user();
        CREATE TRIGGER recommendation_follows AFTER INSERT OR DELETE OR UPDATE ON user_topics
          FOR EACH ROW EXECUTE FUNCTION recommendation_request_user();
        CREATE TRIGGER recommendation_likes AFTER INSERT OR DELETE OR UPDATE ON article_likes
          FOR EACH ROW EXECUTE FUNCTION recommendation_request_user();

        CREATE FUNCTION recommendation_touch_topic(target uuid) RETURNS void LANGUAGE sql AS $$
          INSERT INTO recommendation_topic_events(topic_id) VALUES(target)
          ON CONFLICT (topic_id) DO UPDATE SET version = recommendation_topic_events.version + 1;
        $$;
        CREATE FUNCTION recommendation_touch_article(target uuid)
          RETURNS void LANGUAGE plpgsql AS $$
        DECLARE item record;
        BEGIN
          FOR item IN SELECT topic_id FROM article_topics
            WHERE article_id = target ORDER BY topic_id
          LOOP PERFORM recommendation_touch_topic(item.topic_id); END LOOP;
        END $$;
        CREATE FUNCTION recommendation_catalog_change() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE item record;
        BEGIN
          IF TG_TABLE_NAME = 'article_topics' THEN
            IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_topic(OLD.topic_id); END IF;
            IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_topic(NEW.topic_id); END IF;
          ELSIF TG_TABLE_NAME = 'topic_relations' THEN
            IF TG_OP <> 'INSERT' THEN
              PERFORM recommendation_touch_topic(OLD.topic_id);
              PERFORM recommendation_touch_topic(OLD.related_topic_id);
            END IF;
            IF TG_OP <> 'DELETE' THEN
              PERFORM recommendation_touch_topic(NEW.topic_id);
              PERFORM recommendation_touch_topic(NEW.related_topic_id);
            END IF;
          ELSIF TG_TABLE_NAME = 'topics' THEN
            IF TG_OP = 'DELETE' THEN PERFORM recommendation_touch_topic(OLD.id);
            ELSE PERFORM recommendation_touch_topic(NEW.id); END IF;
          ELSIF TG_TABLE_NAME = 'articles' THEN
            PERFORM recommendation_touch_article(NEW.id);
          ELSIF TG_TABLE_NAME = 'article_origins' THEN
            IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_article(OLD.article_id); END IF;
            IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_article(NEW.article_id); END IF;
          ELSIF TG_TABLE_NAME = 'sources' THEN
            FOR item IN SELECT DISTINCT at.topic_id FROM article_origins ao
              JOIN article_topics at ON at.article_id = ao.article_id
              WHERE ao.source_id = NEW.id ORDER BY at.topic_id
            LOOP PERFORM recommendation_touch_topic(item.topic_id); END LOOP;
          END IF;
          RETURN NULL;
        END $$;
        CREATE TRIGGER recommendation_article_topics
          AFTER INSERT OR UPDATE OR DELETE ON article_topics
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        CREATE TRIGGER recommendation_relations AFTER INSERT OR UPDATE OR DELETE ON topic_relations
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        CREATE TRIGGER recommendation_topics AFTER UPDATE OF status, name OR DELETE ON topics
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        CREATE TRIGGER recommendation_articles
          AFTER UPDATE OF publication_status, review_status, feed_at ON articles
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        CREATE TRIGGER recommendation_origins AFTER INSERT OR UPDATE OR DELETE ON article_origins
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        CREATE TRIGGER recommendation_sources AFTER UPDATE OF approval_status ON sources
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        INSERT INTO user_recommendation_states(user_id) SELECT id FROM user_accounts;
    """)


def downgrade():
    for name, table in (
        ("account", "user_accounts"),
        ("follows", "user_topics"),
        ("likes", "article_likes"),
        ("article_topics", "article_topics"),
        ("relations", "topic_relations"),
        ("topics", "topics"),
        ("articles", "articles"),
        ("origins", "article_origins"),
        ("sources", "sources"),
    ):
        op.execute(f"DROP TRIGGER recommendation_{name} ON {table}")
    op.execute("DROP FUNCTION recommendation_catalog_change()")
    op.execute("DROP FUNCTION recommendation_touch_article(uuid)")
    op.execute("DROP FUNCTION recommendation_touch_topic(uuid)")
    op.execute("DROP FUNCTION recommendation_request_user()")
    for table in (
        "recommendation_topic_events",
        "user_recommendations",
        "user_interests",
        "user_recommendation_states",
    ):
        op.drop_table(table)
