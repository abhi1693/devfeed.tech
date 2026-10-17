"""Persist tag follows and include them in personalized recommendations."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_tags",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Uuid(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_tags_tag_id", "user_tags", ["tag_id"])
    op.add_column("user_recommendations", sa.Column("tag_id", sa.Uuid(), nullable=True))
    op.execute(
        "CREATE TRIGGER recommendation_tag_follows AFTER INSERT OR DELETE OR UPDATE ON user_tags "
        "FOR EACH ROW EXECUTE FUNCTION recommendation_request_user()"
    )


def downgrade():
    op.execute(
        "UPDATE user_recommendation_states SET invalidated = true, next_refresh_at = now() "
        "WHERE user_id IN (SELECT user_id FROM user_tags)"
    )
    op.execute("DELETE FROM user_recommendations WHERE tag_id IS NOT NULL")
    op.execute("DROP TRIGGER recommendation_tag_follows ON user_tags")
    op.drop_column("user_recommendations", "tag_id")
    op.drop_index("ix_user_tags_tag_id", table_name="user_tags")
    op.drop_table("user_tags")
