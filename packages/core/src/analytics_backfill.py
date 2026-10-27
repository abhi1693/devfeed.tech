"""One-time, bounded PostgreSQL history import into ClickHouse.

The live producers must be enabled and old writers drained before imported
history is served. Each destination writer must use stable identities so that
replaying a page after a failed checkpoint is safe.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import sql

from devfeed_core.analytics import AnalyticsUnavailable

MAX_PAGE_SIZE = 1000

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Remove the historical scanner and
# checkpoint table once all supported installations have imported their data.
BACKFILL_DDL = """CREATE TABLE IF NOT EXISTS analytics_backfill_progress (
    import_id String, source LowCardinality(String), page UInt64,
    cursor String, complete UInt8, boundary String
) ENGINE=ReplacingMergeTree(page) ORDER BY (import_id,source)"""


@dataclass(frozen=True, slots=True)
class BackfillSource:
    """Explicitly allowlisted source columns and an idempotent page writer."""

    table: str
    fields: tuple[str, ...]
    keys: tuple[str, ...]
    write: Callable[[list[dict[str, Any]]], Awaitable[None]]
    before: tuple[str, str] | None = None

    def __post_init__(self):
        names = (self.table, *self.fields, *self.keys)
        if (
            not self.table
            or not self.fields
            or not self.keys
            or len(set(self.fields)) != len(self.fields)
            or len(set(self.keys)) != len(self.keys)
            or not set(self.keys) <= set(self.fields)
            or any(
                not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
                for name in names
            )
            or not callable(self.write)
            or (
                self.before is not None
                and (
                    len(self.before) != 2
                    or self.before[0] not in self.fields
                    or not isinstance(self.before[1], str)
                )
            )
        ):
            raise ValueError("Invalid historical analytics source")

    def query(self, *, has_cursor):
        columns: list[sql.Composable] = []
        for name in self.fields:
            columns.extend((sql.Literal(name), sql.Identifier("source", name)))
        keys = sql.SQL(",").join(sql.Identifier("source", name) for name in self.keys)
        statement = sql.SQL("SELECT jsonb_build_object({}) AS record FROM {} AS source").format(
            sql.SQL(",").join(columns), sql.Identifier(self.table)
        )
        conditions = []
        if self.before is not None:
            conditions.append(sql.SQL("{} < %s").format(sql.Identifier("source", self.before[0])))
        if has_cursor:
            bindings = sql.SQL(",").join(sql.Placeholder() for _ in self.keys)
            conditions.append(sql.SQL("({}) > ({})").format(keys, bindings))
        if conditions:
            statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
        return statement + sql.SQL(" ORDER BY {} LIMIT %s").format(keys)


class AnalyticsBackfill:
    """Resume one idempotent page at a time without PostgreSQL state tables."""

    def __init__(self, client, import_id, sources, *, page_size=MAX_PAGE_SIZE):
        if not isinstance(import_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", import_id):
            raise ValueError("A stable historical import identity is required")
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError("Invalid historical import page size")
        sources = tuple(sources)
        if not sources or any(not isinstance(source, BackfillSource) for source in sources):
            raise ValueError("Invalid historical import sources")
        if len({source.table for source in sources}) != len(sources):
            raise ValueError("Duplicate historical import source")
        self.client = client
        self.import_id = import_id
        self.sources = {source.table: source for source in sources}
        self.page_size = page_size

    async def initialize(self):
        await self.client.execute(BACKFILL_DDL)

    async def _progress(self, name):
        rows = await self.client.query(
            "SELECT cursor,page,complete,boundary FROM analytics_backfill_progress FINAL "
            "WHERE import_id={import_id:String} AND source={source:String}",
            {"import_id": self.import_id, "source": name},
        )
        if not rows:
            return None, 0, False
        if len(rows) != 1:
            raise AnalyticsUnavailable("Ambiguous historical import checkpoint")
        try:
            row = rows[0]
            cursor = json.loads(row["cursor"]) if row["cursor"] else None
            page = int(row["page"])
            complete = int(row["complete"])
            boundary = row["boundary"]
            expected_boundary = self.sources[name].before[1] if self.sources[name].before else ""
            if boundary != expected_boundary or page <= 0 or complete not in (0, 1):
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise AnalyticsUnavailable("Invalid historical import checkpoint") from error
        return cursor, page, bool(complete)

    async def complete(self, names):
        """Check that every named historical source has a durable final page."""
        names = tuple(names)
        if (
            not names
            or len(set(names)) != len(names)
            or any(name not in self.sources for name in names)
        ):
            raise ValueError("Invalid historical import source set")
        for name in names:
            _, _, complete = await self._progress(name)
            if not complete:
                return False
        return True

    async def scan_one(self, connection, name):
        """Read and write at most one page; never checkpoint before its insert."""
        source = self.sources.get(name)
        if source is None:
            raise ValueError("Unknown historical import source")
        cursor, page, complete = await self._progress(name)
        if complete:
            return False
        if cursor is not None and (
            not isinstance(cursor, list)
            or len(cursor) != len(source.keys)
            or any(value is None for value in cursor)
        ):
            raise AnalyticsUnavailable("Invalid historical import cursor")
        parameters = (
            *((source.before[1],) if source.before is not None else ()),
            *(cursor or ()),
            self.page_size,
        )
        # Never hold a source transaction open across a ClickHouse request.
        async with connection.transaction():
            await connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
            result = await connection.execute(
                source.query(has_cursor=cursor is not None), parameters
            )
            rows = await result.fetchall()
        records = [row["record"] for row in rows]
        if len(records) > self.page_size or any(
            not isinstance(record, dict)
            or set(record) != set(source.fields)
            or any(record.get(key) is None for key in source.keys)
            for record in records
        ):
            raise AnalyticsUnavailable("Invalid historical import page")
        if records:
            await source.write(records)
            cursor = [records[-1][key] for key in source.keys]
        await self.client.insert(
            "analytics_backfill_progress",
            [
                {
                    "import_id": self.import_id,
                    "source": name,
                    "page": page + 1,
                    "cursor": json.dumps(cursor, separators=(",", ":")) if cursor else "",
                    "complete": int(len(records) < self.page_size),
                    "boundary": source.before[1] if source.before else "",
                }
            ],
        )
        return True
