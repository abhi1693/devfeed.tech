"""Partner measurement fixtures use the same PostgreSQL database as the catalog."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from devfeed_http.partner_measurements import PartnerMeasurements
from sqlalchemy import text


class PartnerAnalyticsFixture:
    def __init__(self, database):
        self.database = database

    def seed(self, asset_id, day, impressions, clicks, *, baseline=False, updated_at=None):
        self.seed_many(
            [
                dict(
                    asset_id=asset_id,
                    day=day,
                    impressions=impressions,
                    clicks=clicks,
                    updated_at=updated_at or datetime.now(UTC),
                )
            ],
            baseline=baseline,
        )

    def seed_many(self, rows, *, baseline=False):
        with self.database.begin() as session:
            for row in rows:
                session.execute(
                    text("""
                    INSERT INTO partner_observations
                        (asset_id,kind,identity,day,revision,impressions,clicks,updated_at)
                    VALUES (:asset_id,:kind,:identity,:day,txid_current(),
                            :impressions,:clicks,:updated_at)
                    ON CONFLICT (asset_id,kind,identity) DO UPDATE
                      SET (revision,impressions,clicks,updated_at) =
                      (excluded.revision,excluded.impressions,excluded.clicks,excluded.updated_at)
                """),
                    dict(updated_at=datetime.now(UTC))
                    | row
                    | {
                        "kind": "partner_baseline" if baseline else "partner_import",
                        "identity": str((row["day"] - date(1970, 1, 1)).days),
                    },
                )

    def publish_claim(self, receipt, kind, claim):
        with self.database.begin() as session:
            session.execute(
                text("""
                INSERT INTO partner_observations
                    (asset_id,kind,identity,day,revision,impressions,clicks,updated_at)
                VALUES (:asset,'partner_event',:identity,:day,0,:impressions,:clicks,:stamp)
                ON CONFLICT DO NOTHING
            """),
                {
                    "asset": receipt.asset_id,
                    "identity": f"{receipt.delivery_id}:{kind}",
                    "day": receipt.issued_at.date(),
                    "impressions": int(kind == "impression"),
                    "clicks": int(kind == "click"),
                    "stamp": claim.recorded_at,
                },
            )

    def daily(self, asset_ids, *, start=None, end=None):
        return asyncio.run(
            PartnerMeasurements(self.database).partner_daily(
                list(asset_ids), start=start or date.min, end=end or date.max
            )
        )

    def events(self):
        with self.database() as session:
            return [
                dict(row)
                for row in session.execute(
                    text("""
                SELECT asset_id::text AS owner, identity, impressions, clicks,
                       updated_at AS last_seen
                FROM partner_observations WHERE kind='partner_event' ORDER BY owner,identity
            """)
                ).mappings()
            ]


@pytest.fixture
def partner_analytics(database):
    with database.begin() as session:
        session.execute(text("TRUNCATE partner_observations"))
    return PartnerAnalyticsFixture(database)
