"""DevFeed 0.0.1: automatic, resumable tag-to-topic identity matching."""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0010_tag_topic_discovery"
down_revision = "0009_relationship_coverage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.schema.CreateSequence(sa.Sequence("tag_topic_catalog_revision")))
    # Consume the initial value so the first subsequent catalog edit advances it.
    op.execute("SELECT nextval('tag_topic_catalog_revision')")
    op.add_column(
        "topics",
        sa.Column(
            "identity_keys", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
    )
    op.add_column(
        "tags", sa.Column("auto_link_topic", sa.Boolean(), nullable=False, server_default="true")
    )
    op.add_column(
        "tags",
        sa.Column("topic_match_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tags",
        sa.Column("topic_match_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column("tags", sa.Column("topic_match_checked_at", sa.DateTime(timezone=True)))
    # Existing links predate automation and must remain editorial decisions.
    op.execute(
        "UPDATE tags SET auto_link_topic = false, topic_match_status = 'manual' "
        "WHERE topic_id IS NOT NULL"
    )
    connection = op.get_bind()
    topics = sa.table(
        "topics",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("aliases", postgresql.ARRAY(sa.String())),
        sa.column("identity_keys", postgresql.ARRAY(sa.Text())),
    )
    cursor = None
    while True:
        query = sa.select(topics).order_by(topics.c.id).limit(500)
        if cursor is not None:
            query = query.where(topics.c.id > cursor)
        rows = connection.execute(query).all()
        if not rows:
            break
        for row in rows:
            keys = sorted(
                {
                    re.sub(r"[\s_-]+", "-", unicodedata.normalize("NFKC", value).strip().casefold())
                    for value in [row.name, row.slug, *(row.aliases or [])]
                    if value.strip()
                }
            )
            connection.execute(
                topics.update().where(topics.c.id == row.id).values(identity_keys=keys)
            )
        cursor = rows[-1].id
    op.create_index("ix_topics_identity_keys", "topics", ["identity_keys"], postgresql_using="gin")
    op.create_index(
        "ix_tags_topic_discovery",
        "tags",
        ["topic_match_revision", "id"],
        postgresql_where=sa.text("auto_link_topic IS true"),
    )


def downgrade():
    op.drop_index("ix_tags_topic_discovery", table_name="tags")
    op.drop_index("ix_topics_identity_keys", table_name="topics")
    op.drop_column("topics", "identity_keys")
    for column in (
        "topic_match_checked_at",
        "topic_match_status",
        "topic_match_revision",
        "auto_link_topic",
    ):
        op.drop_column("tags", column)
    op.execute(sa.schema.DropSequence(sa.Sequence("tag_topic_catalog_revision")))
