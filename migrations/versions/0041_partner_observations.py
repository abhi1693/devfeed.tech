"""Store partner measurements with their original replay identities."""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE partner_observations (
        kind text NOT NULL CHECK (kind IN ('partner_import','partner_baseline','partner_event')),
        asset_id uuid NOT NULL,
        identity text NOT NULL,
        day date NOT NULL,
        revision numeric(20,0) NOT NULL CHECK (revision >= 0),
        impressions numeric(20,0) NOT NULL CHECK (impressions >= 0),
        clicks numeric(20,0) NOT NULL CHECK (clicks >= 0),
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (asset_id, kind, identity)
    )""")
    op.execute(
        "CREATE INDEX ix_partner_observations_asset_day ON partner_observations(asset_id,day)"
    )


def downgrade():
    op.drop_table("partner_observations")
