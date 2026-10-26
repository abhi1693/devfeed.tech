"""Bounded expiry of saved report documents and custom-window demand.

These are replaceable caches. Analytical facts and ClickHouse history are never
deleted here. Maintenance runs outside HTTP requests and preserves live leases.
"""

import time
from datetime import UTC, timedelta
from typing import get_args

from devfeed_core.models import utcnow
from sqlalchemy import text

from devfeed_admin_api.overview_sections import SectionName

IDLE_DAYS = 7
CACHE_ROWS = 1024
MAINTENANCE_SECONDS = 300
PRUNE_BATCH = 100


class ReportRetention:
    def __init__(self, factory):
        self.factory = factory
        self._next_prune = 0.0

    def prune(self, *, now=None, limit=PRUNE_BATCH):
        if not 1 <= limit <= PRUNE_BATCH:
            raise ValueError("Report retention batch must be between 1 and 100")
        clock = time.monotonic()
        if clock < self._next_prune:
            return 0
        # A database failure must not turn every publisher poll into maintenance.
        self._next_prune = clock + MAINTENANCE_SECONDS
        now = (now or utcnow()).astimezone(UTC)
        end = now.date() + timedelta(days=1)
        groups = sorted(set(get_args(SectionName)) - {"operations"})
        parameters = {
            "now": now,
            "cutoff": now - timedelta(days=IDLE_DAYS),
            "groups": groups,
            "windows": [f"{end - timedelta(days=days)}:{end}" for days in (7, 30)],
            "limit": limit,
            "retained": CACHE_ROWS - (1 + 2 * len(groups)),
        }
        removed = 0
        with self.factory.begin() as session:
            session.execute(text("SET LOCAL statement_timeout='2s'"))
            session.execute(text("SET LOCAL lock_timeout='250ms'"))
            for table in ("report_refreshes", "report_snapshots"):
                protected = """NOT ((c."group"='operations' AND c.window_key='current') OR
                  (c."group"=ANY(CAST(:groups AS text[]))
                   AND c.window_key=ANY(CAST(:windows AS text[]))))"""
                unleased = (
                    "(c.lease_until IS NULL OR c.lease_until<=:now)"
                    if table == "report_refreshes"
                    else """NOT EXISTS(SELECT 1 FROM report_refreshes r
                      WHERE r."group"=c."group" AND r.window_key=c.window_key
                      AND r.lease_until>:now)"""
                )
                deleted = session.execute(
                    text(f"""WITH expired AS (
                      SELECT c."group",c.window_key FROM {table} c
                      WHERE c.last_requested_at<:cutoff AND {protected} AND {unleased}
                      ORDER BY c.last_requested_at,c."group",c.window_key
                      LIMIT :limit FOR UPDATE OF c SKIP LOCKED)
                    DELETE FROM {table} c USING expired e
                    WHERE c."group"=e."group" AND c.window_key=e.window_key
                    RETURNING c.window_key"""),
                    parameters,
                ).all()
                removed += len(deleted)
                remaining = limit - len(deleted)
                if not remaining:
                    continue
                # Read at most the fixed cache allowance plus one batch through
                # the receipt index. OFFSET is inside an unlocked CTE: PostgreSQL
                # must not lock all retained rows merely to find old candidates.
                deleted = session.execute(
                    text(f"""WITH overflow AS MATERIALIZED (
                      SELECT c."group",c.window_key,c.last_requested_at FROM {table} c
                      WHERE {protected} AND {unleased}
                      ORDER BY c.last_requested_at DESC,c."group" DESC,c.window_key DESC
                      OFFSET :retained LIMIT :limit),
                    expired AS (
                      SELECT c."group",c.window_key FROM {table} c JOIN overflow e
                        ON c."group"=e."group" AND c.window_key=e.window_key
                        AND c.last_requested_at=e.last_requested_at
                      WHERE {protected} AND {unleased}
                      FOR UPDATE OF c SKIP LOCKED)
                    DELETE FROM {table} c USING expired e
                    WHERE c."group"=e."group" AND c.window_key=e.window_key
                    RETURNING c.window_key"""),
                    parameters | {"limit": remaining},
                ).all()
                removed += len(deleted)
        return removed
