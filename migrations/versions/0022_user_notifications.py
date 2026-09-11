"""Account-owned user notification display preferences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0022_user_notifications"
down_revision = "0021_recommendation_churn"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_accounts",
        sa.Column(
            "notification_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("user_accounts", "notification_settings")
