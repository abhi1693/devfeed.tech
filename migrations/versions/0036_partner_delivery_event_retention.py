"""Index delivery-event days for bounded deduplication cleanup."""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_partner_delivery_event_day", "partner_delivery_events", ["day"])


def downgrade():
    op.drop_index("ix_partner_delivery_event_day", table_name="partner_delivery_events")
