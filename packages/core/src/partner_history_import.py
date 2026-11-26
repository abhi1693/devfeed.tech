"""Copy a finalized ClickHouse partner snapshot into PostgreSQL without duplicate credit."""

import asyncio
from datetime import date, timedelta

from psycopg import Connection

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.config import get_settings
from devfeed_core.partner_claim_backfill import LEGACY_CLAIM_AT

COLUMNS = "asset_id,kind,identity,day,revision,impressions,clicks,updated_at"
SOURCE = """
SELECT owner, kind, identity, day, revision, impressions, clicks,
       toString(last_seen, 'UTC') AS updated_at
FROM analytics_partner_events FINAL
ORDER BY owner,kind,identity
"""


async def require_partner_history(source: AnalyticsClient) -> None:
    expected = {"partner_daily_metrics", "partner_tracked_daily_metrics", "partner_delivery_events"}
    rows = await source.query(
        "SELECT source,complete FROM analytics_backfill_progress FINAL "
        "WHERE import_id='direct-v1' AND source IN {sources:Array(String)}",
        {"sources": sorted(expected)},
    )
    if len(rows) != len(expected) or {r["source"] for r in rows if r["complete"] == 1} != expected:
        raise AnalyticsUnavailable("Partner source history is incomplete")


async def import_snapshot(source: AnalyticsClient, destination: Connection) -> int:
    """Verify every source identity and amount before committing the copy."""
    # A single FINAL query gives one source snapshot including concurrent receipt writers.
    # The final pass must start after the last old-version writer has drained.
    await require_partner_history(source)
    rows = await source.query(SOURCE)
    with destination.transaction(), destination.cursor() as cursor:
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        cursor.execute(
            "CREATE TEMP TABLE incoming_partner_observations "
            "(LIKE partner_observations INCLUDING CONSTRAINTS INCLUDING INDEXES) ON COMMIT DROP"
        )
        with cursor.copy(f"COPY incoming_partner_observations ({COLUMNS}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(
                    (
                        row["owner"],
                        row["kind"],
                        row["identity"],
                        date(1970, 1, 1) + timedelta(days=int(row["day"])),
                        row["revision"],
                        row["impressions"],
                        row["clicks"],
                        row["updated_at"],
                    )
                )
        cursor.execute(f"""
            INSERT INTO partner_observations ({COLUMNS}) SELECT {COLUMNS}
            FROM incoming_partner_observations
            ON CONFLICT (asset_id,kind,identity) DO UPDATE
            SET (day,revision,impressions,clicks,updated_at) =
                (excluded.day,excluded.revision,excluded.impressions,excluded.clicks,excluded.updated_at)
            WHERE excluded.revision > partner_observations.revision
        """)
        cursor.execute(f"""
            SELECT EXISTS (SELECT {COLUMNS} FROM incoming_partner_observations
                           EXCEPT SELECT {COLUMNS} FROM partner_observations)
        """)
        if cursor.fetchone() != (False,):
            raise ValueError("Partner snapshot differs from the destination")
        # The old collector committed the claim before publishing its warehouse event.
        cursor.execute(
            """
            INSERT INTO partner_observations
                (asset_id,kind,identity,day,revision,impressions,clicks,updated_at)
            SELECT d.asset_id, 'partner_event', d.delivery_id::text || ':' || claim.kind,
                   (d.issued_at AT TIME ZONE 'UTC')::date, 0,
                   (claim.kind='impression')::int, (claim.kind='click')::int, claim.stamp
            FROM partner_placement_deliveries d
            CROSS JOIN LATERAL (VALUES ('impression',d.impression_recorded_at),
                                       ('click',d.click_recorded_at)) claim(kind,stamp)
            WHERE claim.stamp IS NOT NULL AND claim.stamp <> %s
            ON CONFLICT (asset_id,kind,identity) DO NOTHING
            """,
            (LEGACY_CLAIM_AT,),
        )
    return len(rows)


async def main() -> None:
    try:
        settings = get_settings()
        dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with AnalyticsClient(settings) as source:
            with Connection.connect(dsn) as destination:
                count = await import_snapshot(source, destination)
    except Exception as error:
        raise SystemExit(f"Partner history verification failed ({type(error).__name__})") from None
    print(f"Verified {count} partner observations")


if __name__ == "__main__":
    asyncio.run(main())
