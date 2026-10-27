"""Direct opens with 30-day viewer retention and compact lifetime/audience states.

Raw viewer keys expire after thirty days. Ingest-time distinct states retain
opaque per-event UUID fingerprints for exact lifetime deduplication and hashed
reader sketches for historical audience counts; they contain no raw viewer
keys. These are not a retry queue or a secondary application projection worker.

The one-time importer freezes old PostgreSQL writers and imports lifetime totals
and retained hourly identities. Lifetime counts are baseline plus the distinct
union of live/history events minus distinct historical events, so retries and
cutover-hour overlap never add the historical event twice.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from clickhouse_connect.driver.external import ExternalData

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable, SqlRecord
from devfeed_core.analytics_backfill import AnalyticsBackfill

MAX_ARTICLES = 100
MAX_IMPORT_ROWS = 1000
MAX_PAGE = 1000


_EVENT_NAMESPACE = uuid.UUID("7394f820-b9e0-4973-a423-c013c89953e6")


def _article(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid article identity") from error


def _instant(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid open timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Open timestamp must include a timezone")
    return value.astimezone(UTC)


def _hour(value: datetime | str) -> datetime:
    return _instant(value).replace(minute=0, second=0, microsecond=0)


def _open(
    article: uuid.UUID | str, viewer: str, opened: datetime | str, *, historical: bool
) -> SqlRecord:
    if not isinstance(viewer, str) or re.fullmatch(r"[0-9a-f]{64}", viewer) is None:
        raise ValueError("Invalid pseudonymous viewer identity")
    identity, hour = _article(article), _hour(opened)
    return {
        "article_id": identity,
        "viewer_key": viewer,
        "opened_hour": hour,
        "event_id": str(uuid.uuid5(_EVENT_NAMESPACE, f"{identity}:{viewer}:{hour.isoformat()}")),
        "historical": int(historical),
    }


def _articles(values: Sequence[uuid.UUID | str]) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ARTICLES:
        raise ValueError("Invalid article identity batch")
    return list(dict.fromkeys(_article(value) for value in values))


class ArticleOpenAnalytics:
    """Write immutable opens and calculate engagement directly in ClickHouse."""

    def __init__(self, client: AnalyticsClient) -> None:
        self.client = client

    async def _cutover(self) -> tuple[datetime | None, bool]:
        rows = await self.client.query(
            "SELECT count() AS entries,min(cutover_hour) AS earliest,"
            "max(cutover_hour) AS latest,max(ready) AS ready "
            "FROM analytics_article_open_cutover"
        )
        if len(rows) != 1 or type(rows[0].get("entries")) is not int:
            raise AnalyticsUnavailable("Invalid article analytics cutover")
        if not rows[0]["entries"]:
            return None, False
        try:
            first, last = _instant(rows[0]["earliest"]), _instant(rows[0]["latest"])
            ready = int(rows[0]["ready"])
        except (KeyError, TypeError, ValueError) as error:
            raise AnalyticsUnavailable("Invalid article analytics cutover") from error
        if first != last or first != _hour(first) or ready not in (0, 1):
            raise AnalyticsUnavailable("Conflicting article analytics cutover")
        return first, bool(ready)

    async def begin_import(self, cutover: datetime | str) -> None:
        """Set the immutable cutover before importing, under one coordinator."""
        cutover = _hour(cutover)
        existing, _ = await self._cutover()
        if existing is not None:
            if existing != cutover:
                raise AnalyticsUnavailable("Conflicting article analytics cutover")
            return
        await self.client.insert(
            "analytics_article_open_cutover", [{"cutover_hour": cutover, "ready": 0}]
        )
        observed, _ = await self._cutover()
        if observed != cutover:
            raise AnalyticsUnavailable("Conflicting article analytics cutover")

    async def mark_ready(self, cutover: datetime | str, importer: AnalyticsBackfill) -> datetime:
        """Publish imported totals only after both historical sources complete."""
        cutover = _hour(cutover)
        existing, ready = await self._cutover()
        if existing != cutover:
            raise AnalyticsUnavailable("Conflicting article analytics cutover")
        if not await importer.complete(["article_engagement", "article_opens"]):
            raise AnalyticsUnavailable("Article analytics import is still running")
        if not ready:
            await self.client.insert(
                "analytics_article_open_cutover", [{"cutover_hour": cutover, "ready": 1}]
            )
        return await self.cutover()

    async def cutover(self) -> datetime:
        """Fail closed until historical totals and retained opens are imported."""
        cutover, ready = await self._cutover()
        if cutover is None or not ready:
            raise AnalyticsUnavailable("Article analytics import is still running")
        return cutover

    async def write_cutover(self) -> datetime:
        """Allow live observations once the import boundary has been fixed."""
        cutover, _ = await self._cutover()
        if cutover is None:
            raise AnalyticsUnavailable("Article analytics import has not started")
        return cutover

    async def record(
        self, article_id: uuid.UUID | str, viewer_key: str, opened_at: datetime | str
    ) -> None:
        """Record one hour's open; retries use the same natural identity."""
        await self.client.insert(
            "analytics_article_opens", [_open(article_id, viewer_key, opened_at, historical=False)]
        )

    async def import_opens(self, records: Sequence[SqlRecord]) -> None:
        """Import one bounded page of retained pre-cutover hourly identities."""
        if not isinstance(records, (list, tuple)) or len(records) > MAX_IMPORT_ROWS:
            raise ValueError("Invalid historical open page")
        try:
            rows = [
                _open(row["article_id"], row["viewer_key"], row["opened_hour"], historical=True)
                for row in records
            ]
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid historical open") from error
        await self.client.insert("analytics_article_opens", rows)

    async def import_totals(self, records: Sequence[SqlRecord]) -> None:
        """Import frozen PostgreSQL lifetime counts without adding them to one another."""
        if not isinstance(records, (list, tuple)) or len(records) > MAX_IMPORT_ROWS:
            raise ValueError("Invalid historical totals page")
        rows = []
        try:
            for row in records:
                count = row["opens"]
                if type(count) is not int or not 0 <= count < 2**64:
                    raise ValueError("Invalid historical open total")
                rows.append({"article_id": _article(row["article_id"]), "opens": count})
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid historical open total") from error
        await self.client.insert("analytics_article_open_baseline", rows)

    async def totals(
        self, article_ids: Sequence[uuid.UUID | str], *, cutover: datetime | str
    ) -> dict[str, int]:
        """Return lifetime totals after the complete import's fixed cutover hour."""
        identities = _articles(article_ids)
        if not identities:
            return {}
        _hour(cutover)
        rows = await self.client.query(
            "SELECT toString(article_id) AS article_id,sum(opens) AS opens FROM ("
            "SELECT article_id,opens FROM analytics_article_open_baseline FINAL "
            "WHERE article_id IN {ids:Array(UUID)} UNION ALL "
            "SELECT article_id,toUInt64(uniqExactMerge(open_state)-"
            "uniqExactMerge(historical_state)) AS opens "
            "FROM analytics_open_lifetime WHERE article_id IN {ids:Array(UUID)} "
            "GROUP BY article_id) "
            "GROUP BY article_id SETTINGS optimize_move_to_prewhere_if_final=0",
            {"ids": identities},
        )
        result = dict.fromkeys(identities, 0)
        for row in rows:
            key = _article(row["article_id"])
            if key not in result or type(row.get("opens")) is not int or row["opens"] < 0:
                raise RuntimeError("Invalid ClickHouse article open total")
            result[key] = row["opens"]
        return result

    async def recent_page(
        self, since: datetime | str, *, after: uuid.UUID | str | None = None, limit: int = MAX_PAGE
    ) -> list[SqlRecord]:
        """Return bounded recent-open counts ordered by article identity.

        The caller must merge every page with PostgreSQL likes and public article
        visibility before ranking; a page alone is not a complete trending feed.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ValueError("Invalid recent article page size")
        parameters = {"since": _instant(since), "limit": limit}
        condition = ""
        if after is not None:
            parameters["after"] = _article(after)
            # The materialized canonical UUID is both the merge cursor and the
            # leading sort key, so later pages can skip earlier article ranges.
            condition = " AND article_key>{after:String}"
        return await self.client.query(
            "SELECT article_key AS article_id,count() AS opens "
            "FROM analytics_article_opens FINAL WHERE opened_hour>={since:DateTime64(6,'UTC')}"
            + condition
            + " GROUP BY article_key ORDER BY article_key LIMIT {limit:UInt32} "
            "SETTINGS optimize_move_to_prewhere_if_final=0",
            parameters,
        )

    async def trending(
        self, since: datetime | str, metadata: bytes, *, limit: int, cursor: int
    ) -> list[SqlRecord]:
        """Rank one authoritative visibility/like snapshot in one server query.

        External query data is transient and never inserted into ClickHouse.
        PostgreSQL has already released its read transaction before this call.
        """
        if not isinstance(metadata, bytes) or len(metadata) > 64 * 1024**2 or len(metadata) % 32:
            raise ValueError("Invalid trending metadata")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid trending page size")
        if type(cursor) is not int or not 0 <= cursor <= 1_000_000:
            raise ValueError("Invalid trending cursor")
        if not metadata:
            return []
        external = ExternalData(
            file_name="visible_articles",
            data=metadata,
            fmt="RowBinary",
            structure="article_id UUID,feed_at Int64,likes UInt64",
        )
        return await self.client.query(
            "WITH recent AS (SELECT article_id,count() AS opens "
            "FROM analytics_article_opens FINAL "
            "WHERE opened_hour>={since:DateTime64(6,'UTC')} GROUP BY article_id) "
            "SELECT toString(v.article_id) AS article_id,"
            "toUInt64(ifNull(o.opens,0)+3*v.likes) AS score "
            "FROM visible_articles AS v LEFT JOIN recent AS o USING article_id "
            "WHERE ifNull(o.opens,0)>0 OR v.likes>0 "
            "ORDER BY score DESC,v.feed_at DESC,toString(v.article_id) DESC "
            "LIMIT {limit:UInt32} OFFSET {offset:UInt32} "
            "SETTINGS optimize_move_to_prewhere_if_final=0",
            {"since": _instant(since), "limit": limit + 1, "offset": cursor},
            external_data=external,
        )
