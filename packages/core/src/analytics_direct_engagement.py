"""Direct ClickHouse storage for partner and successful-search observations.

Callers own authorization, stable event identities and the one-time historical
cutover. Storage contains no query text, account identity, IP or receipt token.
A committed PostgreSQL change and a ClickHouse insert are separate operations;
callers must not assume cross-database atomicity.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

from devfeed_core.analytics import AnalyticsClient, SqlRecord

MAX_BATCH = 1000
MAX_LOOKUP = 1000
_EPOCH = date(1970, 1, 1)
_ZERO_TIME = datetime(1970, 1, 1, tzinfo=UTC)
KINDS = frozenset(
    {"partner_import", "partner_baseline", "partner_event", "search_baseline", "search_event"}
)


def _uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid analytics identity") from error


def _hash(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Invalid search identity")
    return value


def _day(value: object) -> int:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("Invalid analytics day")
    return (value - _EPOCH).days


def _time(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Analytics timestamps require a timezone")
    return value.astimezone(UTC)


def _uint(value: object, *, name: str) -> int:
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError(f"Invalid {name}")
    return value


def _row(
    kind: str,
    owner: str,
    identity: str,
    day: int,
    revision: int,
    impressions: int = 0,
    clicks: int = 0,
    searches: int = 0,
    first_seen: datetime = _ZERO_TIME,
    last_seen: datetime = _ZERO_TIME,
) -> SqlRecord:
    if kind not in KINDS:
        raise ValueError("Invalid analytics kind")
    return {
        "kind": kind,
        "owner": owner,
        "identity": identity,
        "day": day,
        "revision": _uint(revision, name="analytics revision"),
        "impressions": _uint(impressions, name="impressions"),
        "clicks": _uint(clicks, name="clicks"),
        "searches": _uint(searches, name="searches"),
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


class DirectEngagementAnalytics:
    """Store idempotent observations and query only authorized identities.

    ``partner_import`` is the administrator's replaceable total for a day;
    ``partner_baseline`` and ``search_baseline`` are one-time imported totals.
    Live receipt/search observations use stable identities and are deduplicated
    by ClickHouse at read time. Replacing a baseline/import requires the caller
    to provide a strictly increasing revision for the same identity.
    """

    def __init__(self, client: AnalyticsClient) -> None:
        self.client = client

    async def _insert(self, rows: Sequence[SqlRecord]) -> None:
        if not isinstance(rows, (list, tuple)) or len(rows) > MAX_BATCH:
            raise ValueError("Invalid analytics batch")
        if rows:
            tables = {
                "analytics_partner_events"
                if row["kind"].startswith("partner_")
                else "analytics_search_events"
                for row in rows
            }
            if len(tables) != 1:
                raise ValueError("Mixed analytics domains in one batch")
            await self.client.insert(tables.pop(), list(rows))

    async def partner_events(self, events: Sequence[SqlRecord]) -> None:
        if not isinstance(events, (list, tuple)) or len(events) > MAX_BATCH:
            raise ValueError("Invalid partner batch")
        rows = []
        for event in events:
            if not isinstance(event, dict) or set(event) != {
                "asset_id",
                "delivery_id",
                "kind",
                "day",
                "observed_at",
            }:
                raise ValueError("Invalid partner event")
            kind = event["kind"]
            if kind not in {"impression", "click"}:
                raise ValueError("Invalid partner event kind")
            rows.append(
                _row(
                    "partner_event",
                    _uuid(event["asset_id"]),
                    _uuid(event["delivery_id"]) + ":" + kind,
                    _day(event["day"]),
                    0,
                    impressions=int(kind == "impression"),
                    clicks=int(kind == "click"),
                    last_seen=_time(event["observed_at"]),
                )
            )
        await self._insert(rows)

    async def partner_totals(self, totals: Sequence[SqlRecord], *, baseline: bool = False) -> None:
        if (
            not isinstance(totals, (list, tuple))
            or len(totals) > MAX_BATCH
            or type(baseline) is not bool
        ):
            raise ValueError("Invalid partner totals")
        rows = []
        for total in totals:
            if not isinstance(total, dict) or set(total) != {
                "asset_id",
                "day",
                "impressions",
                "clicks",
                "revision",
                "updated_at",
            }:
                raise ValueError("Invalid partner total")
            day = _day(total["day"])
            rows.append(
                _row(
                    "partner_baseline" if baseline else "partner_import",
                    _uuid(total["asset_id"]),
                    str(day),
                    day,
                    total["revision"],
                    impressions=total["impressions"],
                    clicks=total["clicks"],
                    last_seen=_time(total["updated_at"]),
                )
            )
        await self._insert(rows)

    async def search_events(self, events: Sequence[SqlRecord]) -> None:
        if not isinstance(events, (list, tuple)) or len(events) > MAX_BATCH:
            raise ValueError("Invalid search batch")
        rows = []
        for event in events:
            if not isinstance(event, dict) or set(event) != {
                "query_hash",
                "event_id",
                "occurred_at",
            }:
                raise ValueError("Invalid search event")
            timestamp = _time(event["occurred_at"])
            rows.append(
                _row(
                    "search_event",
                    _hash(event["query_hash"]),
                    _uuid(event["event_id"]),
                    0,
                    0,
                    searches=1,
                    first_seen=timestamp,
                    last_seen=timestamp,
                )
            )
        await self._insert(rows)

    async def search_baselines(self, baselines: Sequence[SqlRecord]) -> None:
        if not isinstance(baselines, (list, tuple)) or len(baselines) > MAX_BATCH:
            raise ValueError("Invalid search baselines")
        rows = []
        for baseline in baselines:
            if not isinstance(baseline, dict) or set(baseline) != {
                "query_hash",
                "count",
                "first_seen",
                "last_seen",
                "revision",
            }:
                raise ValueError("Invalid search baseline")
            first, last = _time(baseline["first_seen"]), _time(baseline["last_seen"])
            if first > last:
                raise ValueError("Invalid search baseline timestamps")
            rows.append(
                _row(
                    "search_baseline",
                    _hash(baseline["query_hash"]),
                    "baseline",
                    0,
                    baseline["revision"],
                    searches=baseline["count"],
                    first_seen=first,
                    last_seen=last,
                )
            )
        await self._insert(rows)

    async def partner_daily(
        self,
        asset_ids: Sequence[uuid.UUID | str],
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[SqlRecord]:
        ids = _identities(asset_ids, _uuid)
        start_day = _day(start) if start is not None else None
        end_day = _day(end) if end is not None else None
        if start_day is not None and end_day is not None and start_day > end_day:
            raise ValueError("Invalid analytics date range")
        if not ids:
            return []
        filters: list[str] = []
        params: dict[str, object] = {"owners": ids}
        if start_day is not None:
            filters.append("day >= {start:Int32}")
            params["start"] = start_day
        if end_day is not None:
            filters.append("day <= {end:Int32}")
            params["end"] = end_day
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = await self.client.query(
            "SELECT owner,day,sum(impressions) AS impressions,sum(clicks) AS clicks,"
            "max(last_seen) AS updated_at "
            "FROM (SELECT owner,day,impressions,clicks,last_seen "
            "FROM analytics_partner_events FINAL "
            "WHERE kind IN ('partner_import','partner_baseline','partner_event') "
            "AND owner IN {owners:Array(String)})"
            + where
            + " GROUP BY owner,day ORDER BY owner,day "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            params,
        )
        return [
            row | {"day": date.fromordinal(_EPOCH.toordinal() + int(row["day"]))} for row in rows
        ]

    async def partner_summary(self, asset_ids: Sequence[uuid.UUID | str]) -> list[SqlRecord]:
        """Return all-time partner totals per authorized asset in one bounded query."""
        ids = _identities(asset_ids, _uuid)
        if not ids:
            return []
        return await self.client.query(
            "SELECT owner,sum(impressions) AS impressions,sum(clicks) AS clicks "
            "FROM analytics_partner_events FINAL "
            "WHERE kind IN ('partner_import','partner_baseline','partner_event') "
            "AND owner IN {owners:Array(String)} GROUP BY owner "
            "SETTINGS optimize_move_to_prewhere_if_final=0",
            {"owners": ids},
        )

    async def search_counts(self, query_hashes: Sequence[str]) -> list[SqlRecord]:
        ids = _identities(query_hashes, _hash)
        if not ids:
            return []
        return await self.client.query(
            "SELECT owner AS query_hash,sum(searches) AS successful_count,"
            "min(first_seen) AS first_seen,max(last_seen) AS last_seen "
            "FROM analytics_search_events FINAL "
            "WHERE kind IN ('search_baseline','search_event') AND owner IN {owners:Array(String)} "
            "GROUP BY owner ORDER BY owner",
            {"owners": ids},
        )


def _identities(values: Sequence[object], validator: Callable[[object], str]) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_LOOKUP:
        raise ValueError("Invalid analytics lookup")
    return list(dict.fromkeys(validator(value) for value in values))
