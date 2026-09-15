"""Track candidate invalidation separately from hourly Redis feed orders."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_recommendation_states",
        sa.Column("candidates_dirty", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("user_recommendation_states", sa.Column("ranked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "user_recommendation_states",
        sa.Column("preference_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE user_recommendation_states SET next_refresh_at = LEAST(next_refresh_at, now())"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION public.recommendation_request_user() RETURNS trigger
        LANGUAGE plpgsql AS $function$
        DECLARE target uuid;
        BEGIN
          IF TG_TABLE_NAME = 'user_accounts' THEN target := NEW.id;
          ELSIF TG_OP = 'DELETE' THEN target := OLD.user_id;
          ELSE target := NEW.user_id; END IF;
          INSERT INTO user_recommendation_states(user_id)
            SELECT id FROM user_accounts WHERE id = target
          ON CONFLICT (user_id) DO UPDATE SET invalidated = true, candidates_dirty = true,
            preference_revision = user_recommendation_states.preference_revision + 1,
            next_refresh_at = now(), dispatched_at = NULL, attempts = 0;
          RETURN NULL;
        END $function$
    """)


def downgrade():
    op.execute("""
        CREATE OR REPLACE FUNCTION public.recommendation_request_user() RETURNS trigger
        LANGUAGE plpgsql AS $function$
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
        END $function$
    """)
    for column in ("preference_revision", "ranked_at", "candidates_dirty"):
        op.drop_column("user_recommendation_states", column)
