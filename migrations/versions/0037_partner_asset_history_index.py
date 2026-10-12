"""Index every product asset for candidate-scoped delivery-history ranking."""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_partner_asset_product", "partner_assets", ["product_id", "id"])


def downgrade():
    op.drop_index("ix_partner_asset_product", table_name="partner_assets")
