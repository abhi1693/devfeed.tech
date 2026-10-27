"""Direct ClickHouse article opens and one-time lifetime baselines.

The historical importer must freeze the old PostgreSQL writer, import lifetime
counts and retained hourly identities, and publish its cutover hour before
these reads are exposed. A historical identity wins over a live retry in the
cutover hour, so the lifetime baseline and new events do not double count it.
"""

import re
import uuid
from datetime import UTC, datetime

from devfeed_core.analytics import AnalyticsUnavailable

MAX_ARTICLES = 100
MAX_IMPORT_ROWS = 1000
MAX_PAGE = 1000

OPEN_DDL = """CREATE TABLE IF NOT EXISTS analytics_article_opens (
    opened_hour DateTime64(6, 'UTC'), article_id UUID, viewer_key FixedString(64),
    historical UInt8, article_key String MATERIALIZED toString(article_id)
) ENGINE = ReplacingMergeTree(historical) PARTITION BY toYYYYMM(opened_hour)
ORDER BY (article_key, opened_hour, viewer_key)"""

BASELINE_DDL = """CREATE TABLE IF NOT EXISTS analytics_article_open_baseline (
    article_id UUID, opens UInt64
) ENGINE = ReplacingMergeTree ORDER BY article_id"""

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Remove the import gate after all
# supported installations have completed the one-time historical import.
CUTOVER_DDL = """CREATE TABLE IF NOT EXISTS analytics_article_open_cutover (
    cutover_hour DateTime64(6, 'UTC'), ready UInt8
) ENGINE = MergeTree ORDER BY (cutover_hour, ready)"""


def _article(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid article identity") from error


def _instant(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid open timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Open timestamp must include a timezone")
    return value.astimezone(UTC)


def _hour(value):
    return _instant(value).replace(minute=0, second=0, microsecond=0)


def _open(article, viewer, opened, *, historical):
    if not isinstance(viewer, str) or re.fullmatch(r"[0-9a-f]{64}", viewer) is None:
        raise ValueError("Invalid pseudonymous viewer identity")
    return {
        "article_id": _article(article),
        "viewer_key": viewer,
        "opened_hour": _hour(opened),
        "historical": int(historical),
    }


def _articles(values):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ARTICLES:
        raise ValueError("Invalid article identity batch")
    return list(dict.fromkeys(_article(value) for value in values))


class ArticleOpenAnalytics:
    """Write immutable opens and calculate engagement directly in ClickHouse."""

    def __init__(self, client):
        self.client = client

    async def initialize(self):
        await self.client.execute(OPEN_DDL)
        await self.client.execute(BASELINE_DDL)
        await self.client.execute(CUTOVER_DDL)

    async def _cutover(self):
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

    async def begin_import(self, cutover):
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

    async def mark_ready(self, cutover, importer):
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

    async def cutover(self):
        """Fail closed until historical totals and retained opens are imported."""
        cutover, ready = await self._cutover()
        if cutover is None or not ready:
            raise AnalyticsUnavailable("Article analytics import is still running")
        return cutover

    async def write_cutover(self):
        """Allow live observations once the import boundary has been fixed."""
        cutover, _ = await self._cutover()
        if cutover is None:
            raise AnalyticsUnavailable("Article analytics import has not started")
        return cutover

    async def record(self, article_id, viewer_key, opened_at):
        """Record one hour's open; retries use the same natural identity."""
        await self.client.insert(
            "analytics_article_opens", [_open(article_id, viewer_key, opened_at, historical=False)]
        )

    async def import_opens(self, records):
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

    async def import_totals(self, records):
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

    async def totals(self, article_ids, *, cutover):
        """Return lifetime totals after the complete import's fixed cutover hour."""
        identities = _articles(article_ids)
        if not identities:
            return {}
        rows = await self.client.query(
            "SELECT toString(article_id) AS article_id,sum(opens) AS opens FROM ("
            "SELECT article_id,opens FROM analytics_article_open_baseline FINAL "
            "WHERE article_id IN {ids:Array(UUID)} UNION ALL "
            "SELECT article_id,toUInt64(countIf(historical=0)) AS opens "
            "FROM analytics_article_opens FINAL WHERE article_key IN {ids:Array(String)} "
            "AND opened_hour>={cutover:DateTime64(6,'UTC')} GROUP BY article_id) "
            "GROUP BY article_id SETTINGS optimize_move_to_prewhere_if_final=0",
            {"ids": identities, "cutover": _hour(cutover)},
        )
        result = dict.fromkeys(identities, 0)
        for row in rows:
            key = _article(row["article_id"])
            if key not in result or type(row.get("opens")) is not int or row["opens"] < 0:
                raise RuntimeError("Invalid ClickHouse article open total")
            result[key] = row["opens"]
        return result

    async def recent_page(self, since, *, after=None, limit=MAX_PAGE):
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
