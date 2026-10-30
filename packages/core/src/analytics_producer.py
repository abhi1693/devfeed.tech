"""Application-side direct analytics delivery after successful business commits.

Only compact identities and sanitized previous job attempts are retained in
memory. There is no PostgreSQL analytics queue or database capture trigger.
Delivery has bounded in-memory retries and remains best effort; accepted business
commits are never retried because ClickHouse is unavailable.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import queue
import re
import threading
import uuid
import weakref
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from time import sleep
from typing import Protocol, cast

from clickhouse_connect.driver.exceptions import Error as ClickHouseError
from psycopg import AsyncConnection, sql
from psycopg import Error as PostgreSQLError
from psycopg.rows import dict_row
from sqlalchemy import Column, Engine, Table, Uuid, and_, event, or_, select, tuple_
from sqlalchemy.engine import Connection, ScalarResult
from sqlalchemy.engine.interfaces import DBAPICursor
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import ConnectionPoolEntry, Pool
from sqlalchemy.sql import Select, operators, visitors
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    ColumnElement,
)
from sqlalchemy.sql.schema import ColumnCollectionConstraint

from devfeed_core.analytics import AnalyticsClient, SqlRecord
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_schema import JOB_TABLES, AnalyticsTable
from devfeed_core.analytics_schema import TABLES as CAPTURE_TABLES
from devfeed_core.config import Settings
from devfeed_core.models import Base

TABLES = dict(CAPTURE_TABLES)
for _source, _keys in {
    "user_recommendations": ("user_id", "article_id"),
    "user_sources": ("user_id", "source_id"),
    "user_tags": ("user_id", "tag_id"),
    "tags": ("id",),
}.items():
    TABLES[_source] = AnalyticsTable(_source, _keys, _keys)

logger = logging.getLogger(__name__)
_SESSION = "devfeed_analytics_session"
_PENDING = "devfeed_analytics_changes"
_SAVEPOINTS = "devfeed_analytics_savepoints"
_SKIP = "devfeed_skip_analytics"
MAX_BATCH = 200
MAX_PENDING_TRANSACTIONS = 128
DELIVERY_RETRY_DELAYS = (0.25, 0.5)
PUBLICATION_SOURCES = frozenset(
    {"articles", "article_origins", "article_topics", "article_reviews"}
)
FEED_SOURCES = frozenset(
    {
        "user_accounts",
        "user_topics",
        "user_sources",
        "user_tags",
        "user_interests",
        "user_recommendation_states",
        "user_recommendations",
    }
)
SOURCES = frozenset(TABLES) - {"article_opens", "inference_calls", "topic_decision_calls"}


type WriteStatement = Insert | Update | Delete


class CaptureContext(Protocol):
    """SQLAlchemy execution state plus this listener's per-statement scratch data."""

    compiled: SQLCompiler | None
    compiled_parameters: list[SqlRecord]
    execution_options: Mapping[str, object]
    _devfeed_analytics_parameters: list[SqlRecord]
    _devfeed_analytics_before: list[SqlRecord]
    _devfeed_analytics_pairs: list[tuple[SqlRecord, SqlRecord]]
    _devfeed_analytics_cascades: list[tuple[str, SqlRecord]]


def _capture_table(statement: WriteStatement) -> Table:
    # Capture is installed only for registered ORM metadata tables. SQLAlchemy's
    # wider DML type also allows aliases/joins, which do not enter this adapter.
    return cast(Table, statement.table)


def _identity_value(column: Column[object], value: object) -> str:
    if isinstance(column.type, Uuid):
        return str(uuid.UUID(str(value)))
    return str(value)


def _key(source: str, record: SqlRecord) -> tuple[str, ...]:
    columns = Base.metadata.tables[source].c
    return tuple(_identity_value(columns[name], record[name]) for name in TABLES[source].keys)


def _remember(session: Session, source: str, record: SqlRecord | None) -> None:
    if source not in SOURCES or record is None:
        return
    identity = {name: record[name] for name in TABLES[source].keys}
    pending = session.info.setdefault(_PENDING, {})
    key = (source, _key(source, record))
    previous = pending.get(key)
    # Preserve the original owner, and every completed attempt even if another
    # flush requeues the same job before commit.
    before = previous.get("before_record") if previous else record
    attempts = list(previous.get("attempts", ())) if previous else []
    if source in JOB_TABLES and record.get("status") in {"succeeded", "failed"}:
        attempts.append(record)
    pending[key] = {
        "source_table": source,
        "identity": identity,
        "before_record": before,
        "attempts": attempts,
    }


def _bound_predicate(
    statement: WriteStatement, compiled: SQLCompiler, parameters: SqlRecord
) -> ColumnElement[bool] | None:
    criteria = tuple(getattr(statement, "_where_criteria", ()))
    if not criteria:
        return None

    def replace(
        element: visitors.ExternallyTraversible, **kw: object
    ) -> visitors.ExternallyTraversible | None:
        node = element
        if isinstance(node, BindParameter):
            name = compiled.bind_names.get(node, node.key)
            if name in parameters:
                return cast(
                    visitors.ExternallyTraversible,
                    node._with_value(parameters[name], maintain_key=True, required=False),
                )
        return None

    return visitors.replacement_traverse(and_(*criteria), {}, replace)


def _records(
    connection: Connection, table: Table, predicate: ColumnElement[bool] | None = None
) -> ScalarResult[SqlRecord]:
    spec = TABLES[table.name]
    from sqlalchemy import literal_column

    statement: Select[SqlRecord] = select(literal_column(spec.record_sql(table.name))).select_from(
        table
    )
    if predicate is not None:
        statement = statement.where(predicate)
    return connection.execute(statement.execution_options(**{_SKIP: True})).scalars()


def _insert_candidates(
    connection: Connection,
    statement: WriteStatement,
    compiled: SQLCompiler,
    parameters: Sequence[SqlRecord],
) -> list[SqlRecord]:
    """Read before-images only for updates on conflict, in bounded groups."""
    conflict = getattr(statement, "_post_values_clause", None)
    if not getattr(conflict, "update_values_to_set", None):
        return []
    table = _capture_table(statement)
    columns = getattr(conflict, "inferred_target_elements", None)
    if not columns:
        constraint = getattr(conflict, "constraint_target", None)
        columns = next(
            (
                cast(ColumnCollectionConstraint, item).columns
                for item in table.constraints
                if item.name == constraint
            ),
            None,
        )
    columns = tuple(columns) if columns is not None else tuple(table.primary_key)
    columns = tuple(table.c[column] if isinstance(column, str) else column for column in columns)
    records: list[SqlRecord] = []
    for start in range(0, len(parameters), MAX_BATCH):
        keys = [
            tuple(row[column.name] for column in columns)
            for row in parameters[start : start + MAX_BATCH]
            if all(column.name in row for column in columns)
        ]
        if keys:
            records.extend(_records(connection, table, tuple_(*columns).in_(keys)))
    return records


def _primary_key_bindings(
    statement: WriteStatement, compiled: SQLCompiler
) -> dict[str, str] | None:
    """Recognize exact primary-key updates without interpreting arbitrary SQL."""
    table = _capture_table(statement)
    criteria = list(getattr(statement, "_where_criteria", ()))
    bindings = {}
    while criteria:
        predicate = criteria.pop()
        if isinstance(predicate, BooleanClauseList) and predicate.operator is operators.and_:
            criteria.extend(predicate.clauses)
            continue
        if not isinstance(predicate, BinaryExpression) or predicate.operator is not operators.eq:
            return None
        binding = None
        for column, parameter in (
            (predicate.left, predicate.right),
            (predicate.right, predicate.left),
        ):
            if not isinstance(parameter, BindParameter):
                continue
            if getattr(getattr(column, "table", None), "fullname", None) != table.fullname:
                continue
            key = next((key for key in table.primary_key if key.shares_lineage(column)), None)
            if key is not None:
                binding = key.name, compiled.bind_names.get(parameter, parameter.key)
                break
        if binding is None or binding[0] in bindings:
            return None
        bindings[binding[0]] = binding[1]
    return bindings if len(bindings) == len(table.primary_key) else None


def _update_candidates(
    connection: Connection,
    statement: WriteStatement,
    compiled: SQLCompiler,
    parameters: Sequence[SqlRecord],
) -> tuple[list[SqlRecord], list[tuple[SqlRecord, SqlRecord]]]:
    """Capture ORM batch preimages in bounded reads, retaining per-row assignments."""
    table = _capture_table(statement)
    bindings = _primary_key_bindings(statement, compiled)
    captured: list[SqlRecord] = []
    pairs: list[tuple[SqlRecord, SqlRecord]] = []
    if bindings and all(
        all(name in row and row[name] is not None for name in bindings.values())
        for row in parameters
    ):
        columns = tuple(table.c[key] for key in bindings)
        for start in range(0, len(parameters), MAX_BATCH):
            batch = parameters[start : start + MAX_BATCH]
            keys = [tuple(row[name] for name in bindings.values()) for row in batch]
            rows = list(_records(connection, table, tuple_(*columns).in_(keys)))
            by_key = {
                tuple(_identity_value(column, row[column.name]) for column in columns): row
                for row in rows
            }
            captured.extend(rows)
            pairs.extend(
                (by_key[identity], values)
                for key, values in zip(keys, batch, strict=True)
                if (
                    identity := tuple(
                        _identity_value(column, value)
                        for column, value in zip(columns, key, strict=True)
                    )
                )
                in by_key
            )
        return captured, pairs
    for values in parameters:
        rows = list(_records(connection, table, _bound_predicate(statement, compiled, values)))
        captured.extend(rows)
        pairs.extend((row, values) for row in rows)
    return captured, pairs


def _parameter_rows(context: CaptureContext) -> list[SqlRecord]:
    """Normalize Core VALUES lists and ordinary executemany to column dictionaries."""
    if not getattr(cast(SQLCompiler, context.compiled).statement, "_multi_values", ()):
        return context.compiled_parameters
    output: list[SqlRecord] = []
    for parameters in context.compiled_parameters:
        rows: dict[int, SqlRecord] = {}
        shared = {}
        for name, value in parameters.items():
            match = re.fullmatch(r"(.+)_m([0-9]+)", name)
            if match:
                rows.setdefault(int(match[2]), {})[match[1]] = value
            else:
                shared[name] = value
        output.extend(shared | rows[number] for number in sorted(rows))
    return output


def _ignored_update(operation: WriteStatement, parameters: Sequence[SqlRecord]) -> bool:
    table = _capture_table(operation)
    if not operation.is_update or table.name not in {"articles", "user_accounts"}:
        return False
    assignments = getattr(operation, "_values", None)
    if assignments is not None:
        fields = {getattr(column, "name", column) for column in assignments}
    else:
        fields = {name for row in parameters for name in row if name in table.c}
    if table.name == "user_accounts":
        # Sign-in activity and private reader layouts do not change registered
        # account counts or current feed inputs. Their writes need no analytics.
        return bool(fields) and not fields.intersection(TABLES["user_accounts"].fields)
    return bool(fields) and fields <= {"automation_next_check_at", "automation_started_at"}


def _cascaded_records(
    connection: Connection, target: Table, previous: Sequence[SqlRecord]
) -> list[tuple[str, SqlRecord]]:
    """Keep old child identities before PostgreSQL applies source FK actions."""
    if not previous:
        return []
    changes: list[tuple[str, SqlRecord]] = []
    for child in target.metadata.tables.values():
        if child.name not in SOURCES:
            continue
        for constraint in child.foreign_key_constraints:
            if constraint.ondelete not in {"CASCADE", "SET NULL", "SET DEFAULT"}:
                continue
            links = tuple(constraint.elements)
            if not links or links[0].column.table.fullname != target.fullname:
                continue
            predicates = [
                and_(*(link.parent == row[link.column.name] for link in links))
                for row in previous
                if all(link.column.name in row for link in links)
            ]
            if predicates:
                changes.extend(
                    (child.name, row) for row in _records(connection, child, or_(*predicates))
                )
    return changes


class ApplicationAnalyticsCapture:
    """Observe application transactions without changing results or creating PG state."""

    @classmethod
    def install(cls, session_type: type[Session]) -> None:
        for target, name, callback in (
            (session_type, "after_begin", cls.attach),
            (session_type, "after_transaction_create", cls.savepoint),
            (session_type, "after_soft_rollback", cls.rollback),
            (session_type, "after_commit", cls.committed),
            (Pool, "checkin", cls.detach),
            (Engine, "before_cursor_execute", cls.before),
            (Engine, "after_cursor_execute", cls.after),
        ):
            event.listen(target, name, callback)

    @staticmethod
    def attach(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
        connection.info[_SESSION] = weakref.ref(session)

    @staticmethod
    def savepoint(session: Session, transaction: SessionTransaction) -> None:
        if transaction.nested:
            session.info.setdefault(_SAVEPOINTS, {})[transaction] = dict(
                session.info.get(_PENDING, {})
            )

    @staticmethod
    def rollback(session: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None:
            session.info.pop(_PENDING, None)
            session.info.pop(_SAVEPOINTS, None)
        elif transaction.nested:
            session.info[_PENDING] = session.info.get(_SAVEPOINTS, {}).pop(transaction, {})

    @staticmethod
    def committed(session: Session) -> None:
        if session.in_nested_transaction():
            return
        changes = list(session.info.pop(_PENDING, {}).values())
        session.info.pop(_SAVEPOINTS, None)
        if changes:
            submit(changes)

    @staticmethod
    def detach(dbapi_connection: object, record: ConnectionPoolEntry) -> None:
        record.info.pop(_SESSION, None)

    @staticmethod
    def before(
        connection: Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: object,
        context: CaptureContext,
        executemany: bool,
    ) -> None:
        if connection.dialect.name != "postgresql" or context.execution_options.get(_SKIP):
            return
        reference = connection.info.get(_SESSION)
        session = reference() if reference else None
        compiled = context.compiled
        target = getattr(getattr(compiled, "statement", None), "table", None)
        if session is None or compiled is None or target is None or target.name not in SOURCES:
            return
        operation = cast(WriteStatement, compiled.statement)
        if not (
            getattr(operation, "is_insert", False)
            or getattr(operation, "is_update", False)
            or getattr(operation, "is_delete", False)
        ):
            return
        values_rows = _parameter_rows(context)
        if _ignored_update(operation, values_rows):
            return
        context._devfeed_analytics_parameters = values_rows
        captured = []
        pairs: list[tuple[SqlRecord, SqlRecord]] = []
        if operation.is_insert:
            captured = _insert_candidates(connection, operation, compiled, values_rows)
        else:
            captured, pairs = _update_candidates(connection, operation, compiled, values_rows)
        context._devfeed_analytics_before = captured
        context._devfeed_analytics_pairs = pairs
        context._devfeed_analytics_cascades = (
            _cascaded_records(connection, target, captured) if operation.is_delete else []
        )

    @staticmethod
    def after(
        connection: Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: object,
        context: CaptureContext,
        executemany: bool,
    ) -> None:
        previous = getattr(context, "_devfeed_analytics_before", None)
        if previous is None or cursor.rowcount == 0:
            return
        reference = connection.info.get(_SESSION)
        session = reference() if reference else None
        if session is None:
            return
        operation = cast(WriteStatement, cast(SQLCompiler, context.compiled).statement)
        source = _capture_table(operation).name
        for record in previous:
            _remember(session, source, record)
        for child, record in getattr(context, "_devfeed_analytics_cascades", ()):
            _remember(session, child, record)
        ApplicationAnalyticsCapture.remember_keys(session, context, previous)
        ApplicationAnalyticsCapture.remember_attempts(session, connection, context, previous)

    @staticmethod
    def remember_keys(
        session: Session, context: CaptureContext, previous: Sequence[SqlRecord]
    ) -> None:
        operation = cast(WriteStatement, cast(SQLCompiler, context.compiled).statement)
        source = _capture_table(operation).name
        parameters = context._devfeed_analytics_parameters
        if operation.is_update:
            for record, values in context._devfeed_analytics_pairs:
                identity = {key: values.get(key, record[key]) for key in TABLES[source].keys}
                _remember(session, source, identity)
        if operation.is_insert or operation.is_update:
            for values in parameters:
                if all(name in values and values[name] is not None for name in TABLES[source].keys):
                    # Includes generated defaults and INSERT ... RETURNING without
                    # changing or consuming the caller's result.
                    record = {name: values[name] for name in TABLES[source].keys}
                    _remember(session, source, record)

    @staticmethod
    def remember_attempts(
        session: Session,
        connection: Connection,
        context: CaptureContext,
        previous: Sequence[SqlRecord],
    ) -> None:
        operation = cast(WriteStatement, cast(SQLCompiler, context.compiled).statement)
        source = _capture_table(operation).name
        if source not in JOB_TABLES or operation.is_delete:
            return
        parameters = context._devfeed_analytics_parameters
        identity_rows = list(previous)
        if operation.is_insert:
            identity_rows.extend(
                row for row in parameters if row.get("status") in {"succeeded", "failed"}
            )
        keys = TABLES[source].keys
        identities = list({tuple(row[key] for key in keys) for row in identity_rows})
        for start in range(0, len(identities), MAX_BATCH):
            predicate = and_(
                tuple_(*(operation.table.c[key] for key in keys)).in_(
                    identities[start : start + MAX_BATCH]
                ),
                operation.table.c.status.in_(["succeeded", "failed"]),
            )
            for record in _records(connection, _capture_table(operation), predicate):
                _remember(session, source, record)


async def _publication_rows(
    connection: AsyncConnection[SqlRecord], identities: Collection[str]
) -> dict[str, SqlRecord]:
    if not identities:
        return {}
    identifiers = sorted(identities)
    rows = await (
        await connection.execute(
            "SELECT a.id,a.discovered_at,a.published_to_feed_at,"
            "ARRAY(SELECT DISTINCT o.source_id FROM article_origins o WHERE o.article_id=a.id) "
            "AS source_ids,"
            "ARRAY(SELECT DISTINCT t.topic_id FROM article_topics t WHERE t.article_id=a.id "
            "AND t.role IN ('primary','supporting')) AS topic_ids,"
            "EXISTS(SELECT 1 FROM article_reviews r WHERE r.article_id=a.id AND "
            "r.created_at<=a.published_to_feed_at AND r.action='publish' AND "
            "r.automation<>'{}'::jsonb) "
            "AND NOT EXISTS(SELECT 1 FROM article_reviews r WHERE r.article_id=a.id AND "
            "r.created_at<=a.published_to_feed_at AND r.automation='{}'::jsonb) AS automated "
            "FROM articles a WHERE a.id=ANY(%s::uuid[])",
            (identifiers,),
        )
    ).fetchall()
    return {
        str(row["id"]): {
            "article_id": str(row["id"]),
            "published_at": row["published_to_feed_at"],
            "discovered_at": row["discovered_at"],
            "source_ids": list(map(str, row["source_ids"])),
            "topic_ids": list(map(str, row["topic_ids"])),
            "automated": row["automated"],
            "deleted": row["published_to_feed_at"] is None,
        }
        for row in rows
    }


async def _feed_rows(
    connection: AsyncConnection[SqlRecord], identities: Collection[str]
) -> dict[str, SqlRecord]:
    if not identities:
        return {}
    rows = await (
        await connection.execute(
            "SELECT a.id,a.created_at,s.computed_at,"
            "greatest(coalesce(s.interest_count,0),CASE WHEN "
            "EXISTS(SELECT 1 FROM user_topics t WHERE t.user_id=a.id) OR "
            "EXISTS(SELECT 1 FROM user_sources f WHERE f.user_id=a.id) OR "
            "EXISTS(SELECT 1 FROM user_tags t WHERE t.user_id=a.id) THEN 1 ELSE 0 END) AS "
            "interest_count,s.generation,s.invalidated,s.expires_at,s.next_refresh_at,"
            "EXISTS(SELECT 1 FROM user_recommendations r WHERE r.user_id=a.id) "
            "AS has_recommendations "
            "FROM user_accounts a LEFT JOIN user_recommendation_states s ON s.user_id=a.id "
            "WHERE a.id=ANY(%s::uuid[])",
            (sorted(identities),),
        )
    ).fetchall()
    return {
        str(row["id"]): {
            "user_id": str(row["id"]),
            "created_at": row["created_at"],
            "computed_at": row["computed_at"],
            "interest_count": row["interest_count"],
            "has_generation": row["generation"] is not None,
            "invalidated": bool(row["invalidated"]),
            "has_recommendations": row["has_recommendations"],
            "expires_at": row["expires_at"],
            "next_refresh_at": row["next_refresh_at"],
            "deleted": False,
        }
        for row in rows
    }


async def hydrate_changes(
    connection: AsyncConnection[SqlRecord], changes: Sequence[SqlRecord]
) -> list[SqlRecord]:
    """Hydrate complete current publication/feed inputs for bounded source IDs."""
    articles, users = set(), set()
    for change in changes:
        source = change["source_table"]
        for record in (change.get("record"), change.get("before_record"), change.get("identity")):
            if not record:
                continue
            if source in PUBLICATION_SOURCES:
                value = record.get("id") if source == "articles" else record.get("article_id")
                if value:
                    articles.add(str(value))
            if source in FEED_SOURCES:
                value = record.get("id") if source == "user_accounts" else record.get("user_id")
                if value:
                    users.add(str(value))
    publications = await _publication_rows(connection, articles)
    feeds = await _feed_rows(connection, users)
    result = [
        change
        | {
            "record": change["record"]
            | {"computed_at": feeds.get(str(change["record"]["user_id"]), {}).get("computed_at")}
        }
        if change["source_table"] == "user_interests" and change.get("record")
        else change
        for change in changes
    ]
    for identity in sorted(articles):
        result.append(
            {
                "source_table": "articles",
                "identity": {"id": identity},
                "record": None,
                "publication": publications.get(
                    identity, {"article_id": identity, "deleted": True}
                ),
            }
        )
    for identity in sorted(users):
        result.append(
            {
                "source_table": "user_recommendation_states",
                "identity": {"user_id": identity},
                "record": None,
                "feed": feeds.get(identity, {"user_id": identity, "deleted": True}),
            }
        )
    return result


def _same_completed_attempt(current: SqlRecord | None, previous: SqlRecord) -> bool:
    if (
        current is None
        or current.get("status") not in {"succeeded", "failed"}
        or current.get("finished_at") is None
    ):
        return False
    attempt = current.get("attempts") or 0
    if attempt != (previous.get("attempts") or 0):
        return False
    return bool(attempt) or current.get("finished_at") == previous.get("finished_at")


async def read_current_changes(
    connection: AsyncConnection[SqlRecord], changes: Sequence[SqlRecord]
) -> list[SqlRecord]:
    grouped: dict[str, list[SqlRecord]] = {}
    for change in changes:
        grouped.setdefault(change["source_table"], []).append(change)
    output = []
    for source, items in grouped.items():
        spec = TABLES[source]
        keys = sql.SQL(",").join(map(sql.Identifier, spec.keys))
        placeholders = sql.SQL(",").join(
            sql.SQL("({})").format(sql.SQL(",").join(sql.Placeholder() for _ in spec.keys))
            for _ in items
        )
        statement = sql.SQL("SELECT {} AS record FROM {} WHERE ({}) IN ({})").format(
            sql.SQL(spec.record_sql(source)), sql.Identifier(source), keys, placeholders
        )
        parameters = tuple(item["identity"][key] for item in items for key in spec.keys)
        rows = await (await connection.execute(statement, parameters)).fetchall()
        found = {_key(source, row["record"]): row["record"] for row in rows}
        for item in items:
            record = found.get(_key(source, item["identity"]))
            output.append(item | {"record": record})
            output.extend(
                {"source_table": source, "record": previous, "attempt_only": True}
                for previous in item.get("attempts", ())
                if not _same_completed_attempt(record, previous)
            )
    return output


class DirectAnalyticsPublisher:
    def __init__(self, client: AnalyticsClient, settings: Settings) -> None:
        self.client, self.settings = client, settings

    async def _publish_current(
        self, connection: AsyncConnection[SqlRecord], changes: Sequence[SqlRecord]
    ) -> None:
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout='10s'")
            # Business writes never take this analytics-only lock. Allocation
            # after acquisition orders snapshots even when source commits race.
            await connection.execute("SELECT pg_advisory_xact_lock(%s,%s)", (0x444641, 0))
            sequence = await (
                await connection.execute("SELECT txid_current() AS revision")
            ).fetchone()
            if sequence is None:
                raise RuntimeError("Analytics source ordering is unavailable")
            revision = sequence["revision"]
            current = await read_current_changes(connection, changes)
            hydrated = await hydrate_changes(connection, current)
            for offset in range(0, len(hydrated), 1000):
                await DirectOverviewAnalytics(self.client).publish(
                    hydrated[offset : offset + 1000], revision=revision
                )

    async def publish(self, changes: Sequence[SqlRecord]) -> None:
        import psycopg

        dsn = self.settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=self.settings.database_connect_timeout_seconds,
        ) as connection:
            # ACK every overview/queue observation before releasing its fence.
            # Canonical cards belong to the current-state reconciler, avoiding
            # expensive catalog scans and card construction in short RQ jobs.
            await self._publish_current(connection, changes)


def _delivery_error_fields(error: BaseException) -> dict[str, object]:
    """Only trusted structured codes leave the background writer's error path."""
    fields: dict[str, object] = {"error_type": type(error).__name__}
    cause: BaseException | None = error
    for _ in range(4):
        if cause is None:
            break
        fields["cause_type"] = type(cause).__name__
        if isinstance(cause, ClickHouseError) and type(cause.code) is int:
            fields["clickhouse_code"] = cause.code
        if isinstance(cause, PostgreSQLError):
            state = cause.sqlstate
            if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state):
                fields["sqlstate"] = state
        cause = (
            cause.orig if isinstance(cause, DBAPIError) else cause.__cause__ or cause.__context__
        )
    return fields


class AnalyticsProducer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pending: queue.Queue[list[SqlRecord] | _StopToken] = queue.Queue(
            MAX_PENDING_TRANSACTIONS
        )
        self.pid = os.getpid()
        self._stopping = False
        self._submission_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="analytics-writer", daemon=True)
        self.thread.start()

    def submit(self, changes: list[SqlRecord]) -> bool:
        with self._submission_lock:
            if self._stopping:
                logger.warning("analytics_direct_writer_stopping")
                return False
            try:
                self.pending.put_nowait(changes)
                return True
            except queue.Full:
                logger.warning("analytics_direct_buffer_full")
                return False

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Stop accepting work and drain this process's bounded memory buffer.

        A stalled network call may outlive the deadline; the daemon then exits
        with the process. No durable retry or delivery guarantee is implied.
        """
        if self.pid != os.getpid():
            return True
        with self._submission_lock:
            if not self._stopping:
                self._stopping = True
                # A full queue already wakes the worker; it stops once all
                # accepted items have drained without needing a sentinel.
                with suppress(queue.Full):
                    self.pending.put_nowait(_STOP)
        self.thread.join(max(0.0, timeout))
        stopped = not self.thread.is_alive()
        if not stopped:
            logger.warning("analytics_direct_drain_timed_out")
        return stopped

    def _take_batch(self) -> tuple[list[SqlRecord], int, bool]:
        changes = self.pending.get()
        consumed = 1
        if changes is _STOP:
            return [], consumed, True
        changes = cast(list[SqlRecord], changes)
        stopping = False
        while len(changes) < MAX_BATCH:
            try:
                following = self.pending.get_nowait()
            except queue.Empty:
                break
            consumed += 1
            if following is _STOP:
                stopping = True
                break
            changes = [*changes, *cast(list[SqlRecord], following)]
        return changes, consumed, stopping

    def _deliver(
        self,
        runner: asyncio.Runner,
        publisher: DirectAnalyticsPublisher,
        changes: list[SqlRecord],
        consumed: int,
    ) -> None:
        # Preserve every committed before-image. An association can move A -> B
        # -> C while another process publishes B; all three owners need refresh.
        for start in range(0, len(changes), MAX_BATCH):
            self._publish_chunk(runner, publisher, changes[start : start + MAX_BATCH])
        for _ in range(consumed):
            self.pending.task_done()

    @staticmethod
    def _publish_chunk(
        runner: asyncio.Runner, publisher: DirectAnalyticsPublisher, changes: list[SqlRecord]
    ) -> None:
        for attempt in range(len(DELIVERY_RETRY_DELAYS) + 1):
            try:
                runner.run(publisher.publish(changes))
                return
            except Exception as error:
                retry = attempt < len(DELIVERY_RETRY_DELAYS)
                logger.warning(
                    "analytics_direct_delivery_failed"
                    if retry
                    else "analytics_direct_delivery_abandoned",
                    extra=_delivery_error_fields(error)
                    | {"attempt": attempt + 1, "change_count": len(changes)},
                )
                if retry:
                    # Keep this same bounded chunk, including completed attempts
                    # and old owners. Requests never wait for delivery/backoff.
                    sleep(DELIVERY_RETRY_DELAYS[attempt])

    def _run(self) -> None:
        with asyncio.Runner() as runner:
            client = AnalyticsClient(self.settings)
            client.insert_settings = {"async_insert_busy_timeout_ms": 50}
            publisher = DirectAnalyticsPublisher(client, self.settings)
            try:
                while True:
                    with self._submission_lock:
                        if self._stopping and self.pending.empty():
                            break
                    changes, consumed, stopping = self._take_batch()
                    self._deliver(runner, publisher, changes, consumed)
                    if stopping:
                        break
            finally:
                runner.run(client.close())


class _StopToken:
    """Private typed sentinel for the process-local writer queue."""


_STOP = _StopToken()
_producer: AnalyticsProducer | None = None
_lock = threading.Lock()


def submit(changes: list[SqlRecord]) -> bool:
    from devfeed_core.config import get_settings

    global _producer
    with _lock:
        if _producer is None or _producer.pid != os.getpid():
            _producer = AnalyticsProducer(get_settings())
        producer = _producer
    return producer.submit(changes)


def shutdown(timeout: float = 5.0) -> bool:
    """Drain an existing writer without creating clients or opening services."""
    global _producer
    with _lock:
        producer = _producer
    if producer is None:
        return True
    stopped = producer.shutdown(timeout)
    if stopped:
        with _lock:
            if _producer is producer:
                _producer = None
    return stopped


def _after_fork() -> None:
    # The child inherits neither the daemon thread nor a usable queue/lock.
    # Its first business commit creates a fresh writer owned by the child.
    global _producer, _lock
    _producer = None
    _lock = threading.Lock()


os.register_at_fork(after_in_child=_after_fork)
atexit.register(shutdown)
