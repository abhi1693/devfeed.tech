"""PostgreSQL measurements scoped to the assets authorized by the caller."""

from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

from devfeed_core.analytics import SqlRecord
from sqlalchemy import text
from sqlalchemy.orm import Session

from devfeed_http.dependencies import run_database


class PartnerMeasurements:
    def __init__(self, factory: Callable[[], Session]) -> None:
        self.factory = factory

    async def partner_daily(
        self, asset_ids: Sequence[UUID | str], *, start: date, end: date
    ) -> list[SqlRecord]:
        def read(session: Session) -> list[SqlRecord]:
            rows = session.execute(
                text("""
                SELECT asset_id::text AS owner, day, sum(impressions) AS impressions,
                       sum(clicks) AS clicks, max(updated_at) AS updated_at
                FROM partner_observations
                WHERE asset_id = ANY(CAST(:ids AS uuid[])) AND day BETWEEN :start AND :end
                GROUP BY asset_id,day ORDER BY asset_id,day
            """),
                {"ids": list(map(str, asset_ids)), "start": start, "end": end},
            )
            return [dict(row) for row in rows.mappings()]

        return await run_database(self.factory, read)

    async def partner_summary(self, asset_ids: Sequence[UUID | str]) -> list[SqlRecord]:
        def read(session: Session) -> list[SqlRecord]:
            rows = session.execute(
                text("""
                SELECT asset_id::text AS owner, sum(impressions) AS impressions,
                       sum(clicks) AS clicks
                FROM partner_observations WHERE asset_id = ANY(CAST(:ids AS uuid[]))
                GROUP BY asset_id
            """),
                {"ids": list(map(str, asset_ids))},
            )
            return [dict(row) for row in rows.mappings()]

        return await run_database(self.factory, read)
