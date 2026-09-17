"""Refresh existing recommendation feeds only on their recurring schedule."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
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
          ON CONFLICT (user_id) DO UPDATE SET candidates_dirty = true;
          RETURN NULL;
        END $function$
    """)
    # Align existing feeds with the new cadence, preserving first builds and
    # overdue work. A worker still rechecks the due time before publishing.
    op.execute("""
        UPDATE user_recommendation_states
        SET next_refresh_at = computed_at + interval '6 hours',
            invalidated = false, dispatched_at = NULL
        WHERE computed_at IS NOT NULL AND attempts = 0
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
          ON CONFLICT (user_id) DO UPDATE SET invalidated = true, candidates_dirty = true,
            preference_revision = user_recommendation_states.preference_revision + 1,
            next_refresh_at = now(), dispatched_at = NULL, attempts = 0;
          RETURN NULL;
        END $function$
    """)
