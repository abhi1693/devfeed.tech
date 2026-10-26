"""Cache complete analytical reports by their explicit UTC date interval."""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

WINDOW_CHECK = (
    "(\"group\" = 'operations' AND window_key = 'current' AND days = 1 "
    "AND window_start IS NULL AND window_end IS NULL) OR "
    "(\"group\" <> 'operations' AND window_start IS NOT NULL AND window_end IS NOT NULL "
    "AND window_end > window_start AND days = window_end - window_start "
    "AND window_key = to_char(window_start,'YYYY-MM-DD') || ':' || "
    "to_char(window_end,'YYYY-MM-DD'))"
)


def upgrade():
    # A report reader can request refresh within the same transaction. Release
    # partial locks before retrying instead of blocking readers in a lock cycle.
    op.execute("""DO $body$
      DECLARE deadline timestamptz := clock_timestamp() + interval '30 seconds';
      BEGIN LOOP BEGIN
        LOCK TABLE report_refreshes,report_snapshots IN ACCESS EXCLUSIVE MODE NOWAIT;
        EXIT;
      EXCEPTION WHEN lock_not_available THEN
        IF clock_timestamp() >= deadline THEN RAISE; END IF;
      END; PERFORM pg_sleep(0.05); END LOOP; END $body$""")
    # These two tables contain replaceable published documents and demand only.
    # Source data, transactional outboxes, and retained ClickHouse facts remain intact.
    op.execute("TRUNCATE report_refreshes,report_snapshots")
    op.drop_index("ix_report_refresh_due", table_name="report_refreshes")
    for table, singular in (
        ("report_snapshots", "snapshot"),
        ("report_refreshes", "refresh"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_report_{singular}_days")
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN window_key varchar(21) NOT NULL, "
            "ADD COLUMN window_start date, ADD COLUMN window_end date, "
            "ADD COLUMN last_requested_at timestamptz NOT NULL DEFAULT now()"
        )
        op.execute(f'ALTER TABLE {table} ADD PRIMARY KEY ("group",window_key)')
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_report_{singular}_days "
            "CHECK (days BETWEEN 1 AND 3660)"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_report_{singular}_window CHECK ({WINDOW_CHECK})"
        )
        op.execute(
            f"CREATE INDEX ix_report_{singular}_requested ON "
            f'{table}(last_requested_at,"group",window_key)'
        )
    op.execute(
        'CREATE INDEX ix_report_refresh_due ON report_refreshes(available_at,"group",window_key)'
    )


def downgrade():
    raise RuntimeError(
        "Native analytics requires a coordinated backup restore or forward repair; "
        "schema downgrade is not supported"
    )
