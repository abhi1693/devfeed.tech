"""DevFeed 0.0.1: move saved landing pages to the unified Topics screen."""

from alembic import op

app_version = "0.0.1"
revision = "0012_topics_landing_page"
down_revision = "0011_research_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """UPDATE admin_preferences
        SET settings = jsonb_set(settings, '{defaults,landing_page}', '"/taxonomy/topics"'),
            updated_at = now()
        WHERE settings #>> '{defaults,landing_page}' = '/taxonomy/topics/proposals'"""
    )


def downgrade():
    op.execute(
        """UPDATE admin_preferences
        SET settings = jsonb_set(settings, '{defaults,landing_page}',
                                 '"/taxonomy/topics/proposals"'), updated_at = now()
        WHERE settings #>> '{defaults,landing_page}' = '/taxonomy/topics'"""
    )
