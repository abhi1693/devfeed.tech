"""Application-side direct analytics delivery after successful business commits.

Only compact identities and sanitized previous job attempts are retained in
memory. There is no PostgreSQL analytics queue or database capture trigger.
Delivery is bounded and best effort; accepted business commits are never retried
because ClickHouse is unavailable.
"""

import asyncio
import atexit
import logging
import os
import queue
import re
import threading
import weakref
from contextlib import suppress
from typing import Any

from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import Engine, and_, event, or_, select, tuple_
from sqlalchemy.pool import Pool
from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.elements import BindParameter

from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_schema import JOB_TABLES, AnalyticsTable
from devfeed_core.analytics_schema import TABLES as CAPTURE_TABLES

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


def _key(source, record):
    return tuple(str(record[name]) for name in TABLES[source].keys)


def _remember(session, source, record):
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


def _bound_predicate(statement, compiled, parameters):
    criteria = tuple(getattr(statement, "_where_criteria", ()))
    if not criteria:
        return None

    def replace(element: Any, **kw: Any) -> Any:
        node = element
        if isinstance(node, BindParameter):
            name = compiled.bind_names.get(node, node.key)
            if name in parameters:
                return node._with_value(parameters[name], maintain_key=True, required=False)
        return None

    return visitors.replacement_traverse(and_(*criteria), {}, replace)


def _records(connection, table, predicate=None):
    spec = TABLES[table.name]
    from sqlalchemy import literal_column

    statement: Select[Any] = select(literal_column(spec.record_sql(table.name))).select_from(table)
    if predicate is not None:
        statement = statement.where(predicate)
    return connection.execute(statement.execution_options(**{_SKIP: True})).scalars()


def _insert_candidates(connection, statement, compiled, parameters):
    """Read before-images only for updates on conflict, in bounded groups."""
    conflict = getattr(statement, "_post_values_clause", None)
    if not getattr(conflict, "update_values_to_set", None):
        return []
    table = statement.table
    columns = getattr(conflict, "inferred_target_elements", None)
    if not columns:
        constraint = getattr(conflict, "constraint_target", None)
        columns = next(
            (item.columns for item in table.constraints if item.name == constraint), None
        )
    columns = tuple(columns) if columns is not None else tuple(table.primary_key)
    columns = tuple(table.c[column] if isinstance(column, str) else column for column in columns)
    records = []
    for start in range(0, len(parameters), MAX_BATCH):
        keys = [
            tuple(row[column.name] for column in columns)
            for row in parameters[start : start + MAX_BATCH]
            if all(column.name in row for column in columns)
        ]
        if keys:
            records.extend(_records(connection, table, tuple_(*columns).in_(keys)))
    return records


def _parameter_rows(context):
    """Normalize Core VALUES lists and ordinary executemany to column dictionaries."""
    if not getattr(context.compiled.statement, "_multi_values", ()):
        return context.compiled_parameters
    output: list[dict] = []
    for parameters in context.compiled_parameters:
        rows: dict[int, dict] = {}
        shared = {}
        for name, value in parameters.items():
            match = re.fullmatch(r"(.+)_m([0-9]+)", name)
            if match:
                rows.setdefault(int(match[2]), {})[match[1]] = value
            else:
                shared[name] = value
        output.extend(shared | rows[number] for number in sorted(rows))
    return output


def _clock_only_update(operation, parameters):
    if not operation.is_update or operation.table.name != "articles":
        return False
    assignments = getattr(operation, "_values", None)
    if assignments is not None:
        fields = {getattr(column, "name", column) for column in assignments}
    else:
        keys = {column.name for column in operation.table.primary_key}
        fields = {name for row in parameters for name in row if name in operation.table.c} - keys
    return bool(fields) and fields <= {"automation_next_check_at", "automation_started_at"}


def _dependent_articles(connection, source, records, operation, parameters):
    from devfeed_core.models import ArticleOrigin, ArticleTopic

    if source == "sources":
        relevant = operation.is_delete or any(
            {"name", "approval_status", "enabled"} & set(row) for row in parameters
        )
        table, field = ArticleOrigin.__table__, "source_id"
    elif source == "topics":
        relevant = operation.is_delete
        table, field = ArticleTopic.__table__, "topic_id"
    else:
        return []
    if not relevant or not records:
        return []
    keys = [row["id"] for row in records]
    statement = (
        select(table.c.article_id)
        .where(table.c[field].in_(keys))
        .distinct()
        .execution_options(**{_SKIP: True})
    )
    return list(connection.execute(statement).scalars())


def _cascaded_records(connection, target, previous):
    """Keep old child identities before PostgreSQL applies source FK actions."""
    if not previous:
        return []
    changes: list[tuple[str, dict]] = []
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
    def install(cls, session_type):
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
    def attach(session, transaction, connection):
        connection.info[_SESSION] = weakref.ref(session)

    @staticmethod
    def savepoint(session, transaction):
        if transaction.nested:
            session.info.setdefault(_SAVEPOINTS, {})[transaction] = dict(
                session.info.get(_PENDING, {})
            )

    @staticmethod
    def rollback(session, transaction):
        if transaction.parent is None:
            session.info.pop(_PENDING, None)
            session.info.pop(_SAVEPOINTS, None)
        elif transaction.nested:
            session.info[_PENDING] = session.info.get(_SAVEPOINTS, {}).pop(transaction, {})

    @staticmethod
    def committed(session):
        if session.in_nested_transaction():
            return
        changes = list(session.info.pop(_PENDING, {}).values())
        session.info.pop(_SAVEPOINTS, None)
        if changes:
            submit(changes)

    @staticmethod
    def detach(dbapi_connection, record):
        record.info.pop(_SESSION, None)

    @staticmethod
    def before(connection, cursor, statement, parameters, context, executemany):
        if connection.dialect.name != "postgresql" or context.execution_options.get(_SKIP):
            return
        reference = connection.info.get(_SESSION)
        session = reference() if reference else None
        compiled = context.compiled
        target = getattr(getattr(compiled, "statement", None), "table", None)
        if session is None or target is None or target.name not in SOURCES:
            return
        operation = compiled.statement
        if not (
            getattr(operation, "is_insert", False)
            or getattr(operation, "is_update", False)
            or getattr(operation, "is_delete", False)
        ):
            return
        values_rows = _parameter_rows(context)
        if _clock_only_update(operation, values_rows):
            return
        context._devfeed_analytics_parameters = values_rows
        captured = []
        pairs: list[tuple[dict, dict]] = []
        if operation.is_insert:
            captured = _insert_candidates(connection, operation, compiled, values_rows)
        else:
            for values in values_rows:
                rows = list(
                    _records(connection, target, _bound_predicate(operation, compiled, values))
                )
                captured.extend(rows)
                pairs.extend((row, values) for row in rows)
        context._devfeed_analytics_before = captured
        context._devfeed_analytics_pairs = pairs
        context._devfeed_analytics_cascades = (
            _cascaded_records(connection, target, captured) if operation.is_delete else []
        )
        context._devfeed_analytics_articles = _dependent_articles(
            connection, target.name, captured, operation, values_rows
        )

    @staticmethod
    def after(connection, cursor, statement, parameters, context, executemany):
        previous = getattr(context, "_devfeed_analytics_before", None)
        if previous is None or cursor.rowcount == 0:
            return
        reference = connection.info.get(_SESSION)
        session = reference() if reference else None
        if session is None:
            return
        operation = context.compiled.statement
        source = operation.table.name
        for record in previous:
            _remember(session, source, record)
        for child, record in getattr(context, "_devfeed_analytics_cascades", ()):
            _remember(session, child, record)
        for identity in getattr(context, "_devfeed_analytics_articles", ()):
            _remember(session, "articles", {"id": identity})
        ApplicationAnalyticsCapture.remember_keys(session, context, previous)
        ApplicationAnalyticsCapture.remember_attempts(session, connection, context, previous)

    @staticmethod
    def remember_keys(session, context, previous):
        operation = context.compiled.statement
        source = operation.table.name
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
    def remember_attempts(session, connection, context, previous):
        operation = context.compiled.statement
        source = operation.table.name
        if source not in JOB_TABLES or operation.is_delete:
            return
        parameters = context._devfeed_analytics_parameters
        identities = list(previous)
        if operation.is_insert:
            identities.extend(
                row for row in parameters if row.get("status") in {"succeeded", "failed"}
            )
        keys = TABLES[source].keys
        identities = list({tuple(row[key] for key in keys) for row in identities})
        for start in range(0, len(identities), MAX_BATCH):
            predicate = and_(
                tuple_(*(operation.table.c[key] for key in keys)).in_(
                    identities[start : start + MAX_BATCH]
                ),
                operation.table.c.status.in_(["succeeded", "failed"]),
            )
            for record in _records(connection, operation.table, predicate):
                _remember(session, source, record)


async def _publication_rows(connection, identities):
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


async def _feed_rows(connection, identities):
    if not identities:
        return {}
    rows = await (
        await connection.execute(
            "SELECT a.id,greatest(coalesce(s.interest_count,0),CASE WHEN "
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


async def hydrate_changes(connection, changes):
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
    result = list(changes)
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


def _same_completed_attempt(current, previous):
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


async def read_current_changes(connection, changes):
    grouped: dict[str, list[dict]] = {}
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
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def _publish_current(self, connection, changes):
        from devfeed_core.analytics_direct_cards import publish_cards

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
            await publish_cards(self.client, self.settings, current, revision)
        return current

    async def publish(self, changes):
        import psycopg

        from devfeed_core.analytics_direct_cards import dependent_card_batches

        dsn = self.settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=self.settings.database_connect_timeout_seconds,
        ) as connection:
            current = await self._publish_current(connection, changes)
            # Large catalog changes stream bounded identity pages. Release the
            # analytics lock after each page so other processes can publish.
            async for dependent in dependent_card_batches(connection, current):
                await self._publish_current(connection, dependent)


class AnalyticsProducer:
    def __init__(self, settings):
        self.settings = settings
        self.pending: queue.Queue = queue.Queue(MAX_PENDING_TRANSACTIONS)
        self.pid = os.getpid()
        self._stopping = False
        self._submission_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="analytics-writer", daemon=True)
        self.thread.start()

    def submit(self, changes):
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

    def shutdown(self, timeout=5.0):
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

    def _take_batch(self):
        changes = self.pending.get()
        consumed = 1
        if changes is _STOP:
            return [], consumed, True
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
            changes = [*changes, *following]
        return changes, consumed, stopping

    def _deliver(self, runner, publisher, changes, consumed):
        # Preserve every committed before-image. An association can move A -> B
        # -> C while another process publishes B; all three owners need refresh.
        try:
            for start in range(0, len(changes), MAX_BATCH):
                runner.run(publisher.publish(changes[start : start + MAX_BATCH]))
        except Exception:
            logger.warning("analytics_direct_delivery_failed")
        finally:
            for _ in range(consumed):
                self.pending.task_done()

    def _run(self):
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


_STOP = object()
_producer = None
_lock = threading.Lock()


def submit(changes):
    from devfeed_core.config import get_settings

    global _producer
    with _lock:
        if _producer is None or _producer.pid != os.getpid():
            _producer = AnalyticsProducer(get_settings())
        producer = _producer
    return producer.submit(changes)


def shutdown(timeout=5.0):
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


def _after_fork():
    # The child inherits neither the daemon thread nor a usable queue/lock.
    # Its first business commit creates a fresh writer owned by the child.
    global _producer, _lock
    _producer = None
    _lock = threading.Lock()


os.register_at_fork(after_in_child=_after_fork)
atexit.register(shutdown)
