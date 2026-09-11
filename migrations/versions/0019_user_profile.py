"""User profile overrides, independent of verified sign-in identity."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0019_user_profile"
down_revision = "0018_feed_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_accounts",
        sa.Column(
            "profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )


def downgrade():
    op.drop_column("user_accounts", "profile")
