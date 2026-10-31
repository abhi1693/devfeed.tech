"""Bounded repair of current analytical state, independent of RQ queue names.

Retained completed overview facts and current operational views are repaired in
bounded pages. Lost older attempts and inference calls are never fabricated.
PostgreSQL remains the authority for current jobs; ClickHouse serves the views.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from psycopg import AsyncConnection, sql
from psycopg.errors import LockNotAvailable, QueryCanceled

from devfeed_core.analysis import _PreparedCatalog, reuse_candidates
from devfeed_core.analytics import (
    AnalyticsClient,
    AnalyticsUnavailable,
    ReconciliationBusy,
    SqlRecord,
)
from devfeed_core.analytics_audience_refresh import AudienceMetadataRefresh
from devfeed_core.analytics_direct_cards import CARD_SOURCES, card_identities, shard_of
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_overview_direct import _base, _identity
from devfeed_core.analytics_overview_reconcile import OverviewReconciler
from devfeed_core.analytics_schema import JOB_KINDS, JOB_TABLES, TABLES
from devfeed_core.catalog_cache import SnapshotCache, reuse_snapshots
from devfeed_core.config import Settings

PROGRESS_TABLE = "analytics_reconcile_progress"
FINGERPRINT_TABLE = "analytics_reconcile_fingerprints"
PAGE_SECONDS = 20
MAX_ACTIVE_JOBS = 20_000
PUBLISH_LOCK = (0x444641, 0)
RECONCILE_LOCK = (0x44464352, 0)
CARD_KINDS = tuple(CARD_SOURCES.values())
FINAL_GUARDS = (
    " SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0"
)
type Connections = Callable[[], AbstractAsyncContextManager[AsyncConnection[SqlRecord]]]
logger = logging.getLogger(__name__)


def _finished_builder(task: asyncio.Task[list[SqlRecord]]) -> None:
    """Observe an orphan's result without disclosing SQL, source data or credentials."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.warning(
            "analytics_current_builder_failed", extra={"error_type": type(error).__name__}
        )


class Coverage(TypedDict):
    completed_at: datetime | None
    source_at: datetime | None
    pending: bool
    max_age_seconds: int


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError("Invalid reconciliation timestamp")


def card_scope(kind: str) -> str:
    if kind not in CARD_KINDS:
        raise ValueError("Unknown analytical card kind")
    return "cards:" + kind


async def read_reconciliation_status(
    client: AnalyticsClient, policy: str, *, card_kinds: Sequence[str] = CARD_KINDS
) -> dict[str, Coverage]:
    """Combine only independently acknowledged populations requested by a reader."""
    kinds = tuple(sorted(set(card_kinds)))
    if not kinds:
        raise ValueError("Card coverage requires a population")
    scopes = tuple(card_scope(kind) for kind in kinds)
    settings = client.settings
    bounds = {
        "queues": max(60, math.ceil(2 * settings.analytics_reconcile_poll_seconds)),
        "cards": max(60, math.ceil(2 * settings.analytics_reconcile_sweep_seconds)),
    }
    result: dict[str, Coverage] = {
        scope: {"completed_at": None, "source_at": None, "pending": True, "max_age_seconds": age}
        for scope, age in bounds.items()
    }
    try:
        rows = await client.query(
            "/* analytics:reconciliation-status */ SELECT scope,completed_at,source_at,pending "
            f"FROM {PROGRESS_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND scope IN {scopes:Array(String)}" + FINAL_GUARDS,
            {"policy": policy, "scopes": ["queues", *scopes]},
        )
    except AnalyticsUnavailable as error:
        cause = error.__cause__
        if cause is not None and (
            getattr(cause, "code", None) == 60 or re.search(r"\bCode:\s*60\b", str(cause))
        ):
            return result
        raise
    observed: dict[str, Coverage] = {}
    for row in rows:
        scope = row["scope"]
        if scope not in ("queues", *scopes) or scope in observed or row["pending"] not in (0, 1):
            raise ValueError("Ambiguous reconciliation coverage")
        observed[scope] = {
            "completed_at": _timestamp(row["completed_at"]),
            "source_at": _timestamp(row["source_at"]),
            "pending": bool(row["pending"]),
            "max_age_seconds": bounds["queues" if scope == "queues" else "cards"],
        }
    if "queues" in observed:
        result["queues"] = observed["queues"]
    populations = [observed.get(scope) for scope in scopes]
    if all(
        row is not None and row["completed_at"] is not None and row["source_at"] is not None
        for row in populations
    ):
        covered = [row for row in populations if row is not None]
        result["cards"] = {
            "completed_at": max(
                row["completed_at"] for row in covered if row["completed_at"] is not None
            ),
            "source_at": min(row["source_at"] for row in covered if row["source_at"] is not None),
            "pending": any(row["pending"] for row in covered),
            "max_age_seconds": bounds["cards"],
        }
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _queue_hash(row: SqlRecord) -> str:
    return _digest(
        {
            key: _timestamp(row.get(key)) if key == "created_at" else row.get(key)
            for key in ("deleted", "task", "status", "attempt", "created_at")
        }
    )


def _queue_row(job: SqlRecord, revision: int) -> SqlRecord:
    record = job["record"]
    row = _base("queue", job["id"], revision, deleted=not job["valid"])
    row.update(
        task=job["kind"],
        status=record["status"],
        attempt=record.get("attempts") or 0,
        created_at=_timestamp(record.get("created_at")),
    )
    return row


@dataclass
class QueueCycle:
    revision: int
    source_at: datetime
    jobs: list[SqlRecord]
    valid: set[str]
    previous: dict[str, SqlRecord]
    position: int = 0
    absent: list[SqlRecord] = field(default_factory=list)
    absent_position: int = 0
    cursor: str | None = None
    phase: str = "active"


class CurrentStateReconciler:
    """One bounded current-state page, with an independent failover coordinator.

    The coordinator connection is idle/autocommit between pages: it pins no
    PostgreSQL snapshot or business lock. Its session lock prevents two replicas
    from interleaving an in-memory, bounded active-job snapshot. Publisher locks
    are held only while selecting source identities and reserving their fence.
    The connection factory must use direct PostgreSQL or a session-mode pooler;
    transaction-mode pooling cannot preserve session advisory lock ownership.
    """

    def __init__(
        self,
        client: AnalyticsClient,
        settings: Settings,
        connect: Connections | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        from devfeed_core.analytics_connections import postgres_connections

        self.client, self.settings = client, settings
        self.connect = connect or postgres_connections(
            settings.database_url, connect_timeout=settings.database_connect_timeout_seconds
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = pipeline_policy_version(settings)
        self.batch = settings.analytics_reconcile_batch_size
        self.progress: dict[str, SqlRecord] = {}
        self.queue_cycle: QueueCycle | None = None
        self._leader: AsyncConnection[SqlRecord] | None = None
        self._leader_context: AbstractAsyncContextManager[AsyncConnection[SqlRecord]] | None = None
        self._version = 0
        self._turn = 0
        self._card_turn = 0
        self._todo_turn = 0
        self._builder: asyncio.Task[list[SqlRecord]] | None = None
        self._interrupted_page_builder: asyncio.Task[list[SqlRecord]] | None = None
        self._card_catalogs: SnapshotCache = {}
        self._card_ranking: list[_PreparedCatalog] = []
        self._audience_metadata = AudienceMetadataRefresh(client, self._source_snapshot)
        self._overview = OverviewReconciler(
            client, settings, self._source_snapshot, policy=self.policy, clock=self.clock
        )

    async def close(self) -> None:
        context, self._leader_context = self._leader_context, None
        self._leader = None
        self.queue_cycle = None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def _lead(self) -> bool:
        if self._leader is not None:
            await self._leader.execute("SELECT 1")
            return True
        context = self.connect()
        connection = await context.__aenter__()
        try:
            await connection.execute("SET statement_timeout='5s'")
            row = await (
                await connection.execute(
                    "SELECT pg_try_advisory_lock(%s,%s) AS acquired", RECONCILE_LOCK
                )
            ).fetchone()
            if row is None or row["acquired"] is not True:
                await context.__aexit__(None, None, None)
                return False
        except BaseException:
            await context.__aexit__(None, None, None)
            raise
        self._leader, self._leader_context = connection, context
        return True

    async def _load(self) -> None:
        rows = await self.client.query(
            f"SELECT * FROM {PROGRESS_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND scope IN {scopes:Array(String)}" + FINAL_GUARDS,
            {
                "policy": self.policy,
                "scopes": [
                    "queues",
                    "cards",
                    "active-cards",
                    "sweep",
                    *(card_scope(kind) for kind in CARD_KINDS),
                ],
            },
        )
        self.progress = {row["scope"]: row for row in rows}
        if len(self.progress) != len(rows):
            raise AnalyticsUnavailable("Ambiguous reconciliation progress")
        self._version = max((int(row["version"]) for row in rows), default=0)

    def _state(self, scope: str) -> dict[str, Any]:
        raw = self.progress.get(scope, {}).get("cursor", "")
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            raise AnalyticsUnavailable("Invalid current-state cursor")
        for target in value.get("todo", []):
            if not isinstance(target, str) or ":" not in target:
                raise AnalyticsUnavailable("Invalid current card target")
            kind, identity = target.split(":", 1)
            if kind not in CARD_KINDS:
                raise AnalyticsUnavailable("Invalid current card target")
            uuid.UUID(identity)
        return value

    async def _save(self, scope: str, state: dict[str, Any], **updates: object) -> None:
        previous = self.progress.get(scope, {})
        self._version = max(time.time_ns(), self._version + 1)
        row = {
            "policy": self.policy,
            "scope": scope,
            "version": self._version,
            "source_at": _timestamp(previous.get("source_at")),
            "completed_at": _timestamp(previous.get("completed_at")),
            "pending": int(previous.get("pending", 1)),
            "cursor": json.dumps(state, separators=(",", ":")),
            **updates,
        }
        await self.client.insert(PROGRESS_TABLE, [row])
        self.progress[scope] = row

    async def _enqueue(self, owners: Sequence[str]) -> None:
        if not owners:
            return
        state = {"todo": sorted(set(self._state("cards").get("todo", ())) | set(owners))}
        if len(state["todo"]) > MAX_ACTIVE_JOBS:
            raise AnalyticsUnavailable("Current-card target set exceeds its bound")
        kinds = {owner.split(":", 1)[0] for owner in owners}
        for kind in sorted(kinds):
            scope = card_scope(kind)
            await self._save(scope, self._state(scope), pending=1)
        await self._save("cards", state, pending=1)

    @asynccontextmanager
    async def _source_snapshot(
        self,
    ) -> AsyncIterator[tuple[AsyncConnection[SqlRecord], int, datetime]]:
        async with self.connect() as connection:
            await connection.execute("SET statement_timeout='10s'")
            # Acquire before BEGIN: the first RR SELECT establishes its snapshot
            # before evaluating SQL functions, so an xact-lock SELECT would be
            # too late to order that snapshot against the previous publisher.
            try:
                # Queue behind admitted publishers instead of starving whenever
                # another publisher is already waiting. Native async I/O keeps
                # the event loop free; the statement timeout bounds this wait.
                await connection.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)
            except (LockNotAvailable, QueryCanceled) as error:
                raise ReconciliationBusy("publication_busy") from error
            locked = True
            try:
                await connection.execute("SET statement_timeout='5s'")
                async with connection.transaction():
                    await connection.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    row = await (
                        await connection.execute(
                            "SELECT txid_current() AS revision,clock_timestamp() AS observed_at"
                        )
                    ).fetchone()
                    if row is None:
                        raise AnalyticsUnavailable("Current-state ordering is unavailable")
                    # The fixed snapshot and F are now ordered. Later publishers
                    # may proceed while bounded source metadata is read.
                    await connection.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)
                    locked = False
                    yield (
                        connection,
                        int(row["revision"]),
                        _timestamp(row["observed_at"]) or self.clock(),
                    )
            finally:
                # Closing this dedicated connection also releases a lock if an
                # interrupted network exchange prevents explicit unlock.
                if locked:
                    await connection.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)

    async def _snapshot(self) -> QueueCycle:
        jobs: list[SqlRecord] = []
        async with self._source_snapshot() as snapshot:
            connection, revision, source_at = snapshot
            for table in JOB_TABLES:
                spec = TABLES[table]
                statement = sql.SQL(
                    "SELECT {} AS record,source.xmin::text AS stamp,lease_until,"
                    "lease_token IS NOT NULL AS leased FROM {} AS source "
                    "WHERE status IN ('queued','running') LIMIT %s"
                ).format(sql.SQL(spec.record_sql("source")), sql.Identifier(table))
                rows = await (
                    await connection.execute(statement, (MAX_ACTIVE_JOBS - len(jobs) + 1,))
                ).fetchall()
                for row in rows:
                    record = row["record"]
                    kind = JOB_KINDS[table]
                    queue_id = _identity("queue", kind, record["id"])
                    owners = card_identities([{"source_table": table, "record": record}])
                    leased = bool(row["leased"]) and (
                        (lease_until := _timestamp(row["lease_until"])) is not None
                        and lease_until > source_at
                    )
                    jobs.append(
                        {
                            "id": queue_id,
                            "record": record,
                            "kind": kind,
                            "valid": record["status"] == "queued" or leased,
                            "owners": [
                                f"{owner}:{value}" for owner, ids in owners.items() for value in ids
                            ],
                            "fingerprint": _digest(
                                [record, row["stamp"], row["lease_until"], row["leased"]]
                            ),
                        }
                    )
                if len(jobs) > MAX_ACTIVE_JOBS:
                    raise AnalyticsUnavailable("Active job snapshot exceeds its bound")
        # Source snapshot is closed before any ClickHouse I/O.
        previous_rows = await self.client.query(
            f"SELECT * FROM {FINGERPRINT_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND kind IN {kinds:Array(String)} AND deleted=0 LIMIT {limit:UInt32}" + FINAL_GUARDS,
            {
                "policy": self.policy,
                "kinds": ["queue:" + name for name in JOB_KINDS.values()],
                "limit": MAX_ACTIVE_JOBS + 1,
            },
        )
        if len(previous_rows) > MAX_ACTIVE_JOBS:
            raise AnalyticsUnavailable("Current job fingerprint set exceeds its bound")
        previous = {str(row["entity_id"]): row for row in previous_rows}
        present = {job["id"] for job in jobs}
        valid = {job["id"] for job in jobs if job["valid"]}
        targets = await self._queue_targets([job["id"] for job in jobs])
        # Unchanged sources are still checked against their actual target. They
        # do not consume a repair page or rewrite fingerprints on every poll.
        jobs = [
            job
            for job in jobs
            if (before := previous.get(job["id"])) is None
            or before["fingerprint"] != job["fingerprint"]
            or (target := targets.get(job["id"])) is None
            or _queue_hash(target) != _queue_hash(_queue_row(job, revision))
        ]
        return QueueCycle(
            revision,
            source_at,
            jobs,
            valid,
            previous,
            absent=[row for identity, row in previous.items() if identity not in present],
        )

    async def _queue_targets(self, identifiers: Sequence[str]) -> dict[str, SqlRecord]:
        if not identifiers:
            return {}
        rows = await self.client.query(
            "SELECT id,revision,deleted,task,status,attempt,created_at "
            "FROM analytics_pipeline_events FINAL WHERE domain='queue' AND id IN {ids:Array(UUID)}"
            + FINAL_GUARDS,
            {"ids": list(identifiers)},
        )
        return {str(row["id"]): row for row in rows}

    async def _active_queue_page(self, cycle: QueueCycle) -> None:
        page = cycle.jobs[cycle.position : cycle.position + self.batch]
        targets = await self._queue_targets([job["id"] for job in page])
        writes, fingerprints, owners = [], [], []
        for job in page:
            row = _queue_row(job, cycle.revision)
            previous = cycle.previous.get(job["id"])
            changed = previous is None or previous["fingerprint"] != job["fingerprint"]
            if changed:
                owners.extend(job["owners"])
                if previous is not None:
                    owners.extend(previous["owners"])
            target = targets.get(job["id"])
            if target is None or _queue_hash(target) != _queue_hash(row):
                writes.append(row)
            if changed or (previous is not None and previous["target_hash"] != _queue_hash(row)):
                fingerprints.append(
                    {
                        "policy": self.policy,
                        "kind": "queue:" + job["kind"],
                        "entity_id": job["id"],
                        "version": cycle.revision,
                        "fingerprint": job["fingerprint"],
                        "target_hash": _queue_hash(row),
                        "owners": job["owners"],
                        "deleted": 0,
                    }
                )
        if writes:
            await self.client.insert("analytics_pipeline_events", writes)
        await self._enqueue(owners)
        if fingerprints:
            await self.client.insert(FINGERPRINT_TABLE, fingerprints)
        cycle.position += len(page)
        if cycle.position == len(cycle.jobs):
            cycle.phase = "absent"

    async def _absent_queue_page(self, cycle: QueueCycle) -> None:
        page = cycle.absent[cycle.absent_position : cycle.absent_position + self.batch]
        await self._enqueue([owner for row in page for owner in row["owners"]])
        if page:
            await self.client.insert(
                FINGERPRINT_TABLE,
                [row | {"version": cycle.revision, "deleted": 1} for row in page],
            )
        cycle.absent_position += len(page)
        if cycle.absent_position == len(cycle.absent):
            cycle.phase = "target"

    async def _absent_queue_targets(self, cycle: QueueCycle) -> bool:
        bound = " AND id>{cursor:UUID}" if cycle.cursor is not None else ""
        rows = await self.client.query(
            "SELECT id FROM analytics_pipeline_events FINAL WHERE domain='queue' "
            "AND deleted=0 AND status IN ('queued','running') AND revision<={fence:UInt64}"
            " AND id NOT IN {valid:Array(UUID)}"
            + bound
            + " ORDER BY id LIMIT {limit:UInt32}"
            + FINAL_GUARDS,
            {
                "fence": cycle.revision,
                "cursor": cycle.cursor,
                "limit": self.batch,
                "valid": sorted(cycle.valid),
            },
        )
        missing = [str(row["id"]) for row in rows if str(row["id"]) not in cycle.valid]
        if missing:
            await self.client.insert(
                "analytics_pipeline_events",
                [_base("queue", identity, cycle.revision, deleted=True) for identity in missing],
            )
        if len(rows) < self.batch:
            await self._save(
                "queues", {}, pending=0, source_at=cycle.source_at, completed_at=self.clock()
            )
            self.queue_cycle = None
            return True
        cycle.cursor = str(rows[-1]["id"])
        return False

    async def _queue_step(self) -> None:
        cycle = self.queue_cycle
        if cycle is None:
            cycle = await self._snapshot()
            self.queue_cycle = cycle
            await self._save("queues", {}, pending=1)
        if cycle.phase == "active":
            await self._active_queue_page(cycle)
        elif cycle.phase == "absent":
            await self._absent_queue_page(cycle)
        elif await self._absent_queue_targets(cycle):
            return
        await self._save(
            "queues",
            {
                "phase": cycle.phase,
                "position": cycle.position,
                "absent_position": cycle.absent_position,
                "cursor": cycle.cursor,
            },
            pending=1,
        )

    async def _card_targets(self, kind: str, identifiers: Sequence[str]) -> dict[str, SqlRecord]:
        if not identifiers:
            return {}
        # Canonical inserts validate the UUID-derived shard; keep lookups on the full key.
        rows = await self.client.query(
            "SELECT entity_id,version,semantic_hash,deleted,next_transition_at "
            "FROM analytics_card_canonical FINAL WHERE policy={policy:String} "
            "AND kind={kind:String} AND shard IN {shards:Array(UInt16)} "
            "AND entity_id IN {ids:Array(UUID)}" + FINAL_GUARDS,
            {
                "policy": self.policy,
                "kind": kind,
                "shards": sorted({shard_of(identity) for identity in identifiers}),
                "ids": list(identifiers),
            },
        )
        result = {str(row["entity_id"]): row for row in rows}
        if len(result) != len(rows):
            raise AnalyticsUnavailable("Ambiguous current card target")
        return result

    async def _build(self, kind: str, identifiers: list[str], revision: int) -> list[SqlRecord]:
        from devfeed_core.analytics_direct_cards import build_cards

        if self._builder is not None and not self._builder.done():
            raise ReconciliationBusy("builder_busy")

        def build() -> list[SqlRecord]:
            # Revisions are checked inside each source snapshot. A changed catalog
            # releases its prepared index before loading the replacement generation.
            with (
                reuse_snapshots(
                    retained=self._card_catalogs, on_invalidate=self._card_ranking.clear
                ),
                reuse_candidates(retained=self._card_ranking),
            ):
                return build_cards(self.settings, kind, identifiers, revision)

        task = asyncio.create_task(asyncio.to_thread(build))
        self._builder = task
        try:
            # Timeout/cancellation cannot stop the native thread. Keep its task
            # alive and refuse a second builder until it finishes; no abandoned
            # result is ever inserted or acknowledged by a later page.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._interrupted_page_builder = task
            task.add_done_callback(_finished_builder)
            raise
        finally:
            if task.done():
                self._builder = None

    async def _refresh(self, kind: str, identifiers: Sequence[str]) -> bool:
        from devfeed_core.analytics_reconcile_sources import card_fingerprints

        if not identifiers:
            return False
        async with self._source_snapshot() as snapshot:
            connection, revision, _ = snapshot
            signatures = await card_fingerprints(connection, kind, identifiers)
        rows = await self.client.query(
            f"SELECT * FROM {FINGERPRINT_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND kind={kind:String} AND entity_id IN {ids:Array(UUID)} AND deleted=0"
            + FINAL_GUARDS,
            {"policy": self.policy, "kind": "card:" + kind, "ids": list(identifiers)},
        )
        cached = {str(row["entity_id"]): row for row in rows}
        targets = await self._card_targets(kind, identifiers)
        now = self.clock()
        selected = []
        for identity in identifiers:
            before, target = cached.get(identity), targets.get(identity)
            signature = signatures.get(identity, "missing")
            due = target is not None and (
                (transition := _timestamp(target.get("next_transition_at"))) is not None
                and transition <= now
            )
            if (
                not signature
                or before is None
                or target is None
                or due
                or before["fingerprint"] != signature
                or before["target_hash"] != target["semantic_hash"]
            ):
                selected.append(identity)
        if not selected:
            return False
        built = await self._build(kind, selected, revision)
        if {str(row["entity_id"]) for row in built} != set(selected):
            raise AnalyticsUnavailable("Incomplete current card construction")
        await self.client.insert("analytics_card_canonical", built)
        observed = await self._card_targets(kind, selected)
        # The builder uses its own later PG snapshot. A delayed higher-version
        # live publisher can win: never acknowledge a fingerprint unless the
        # actual canonical target agrees. Recheck this hash on unchanged inputs
        # too, so a stale writer arriving after verification cannot freeze it.
        if any(
            (target := observed.get(str(row["entity_id"]))) is None
            or target["semantic_hash"] != row["semantic_hash"]
            for row in built
        ):
            raise AnalyticsUnavailable("Current card changed during publication")
        await self.client.insert(
            FINGERPRINT_TABLE,
            [
                {
                    "policy": self.policy,
                    "kind": "card:" + kind,
                    "entity_id": row["entity_id"],
                    "version": revision,
                    "fingerprint": signatures.get(str(row["entity_id"]), "missing"),
                    "target_hash": row["semantic_hash"],
                    "owners": [],
                    "deleted": 0,
                }
                for row in built
            ],
        )
        return True

    async def _todo(self) -> bool:
        # Keep the durable dirty-owner ledger across upgrades; whole-population
        # coverage lives only in the independent cards:<kind> scopes.
        state = {"todo": self._state("cards").get("todo", [])}
        todo = state["todo"]
        if not todo:
            return False
        for offset in range(len(CARD_KINDS)):
            index = (self._todo_turn + offset) % len(CARD_KINDS)
            kind = CARD_KINDS[index]
            page = [value for value in todo if value.startswith(kind + ":")][: self.batch]
            if page:
                self._todo_turn = (index + 1) % len(CARD_KINDS)
                break
        else:
            raise AnalyticsUnavailable("Invalid current card target")
        await self._refresh(kind, [value.split(":", 1)[1] for value in page])
        covered = set(page)
        state["todo"] = [value for value in todo if value not in covered]
        await self._save("cards", state, pending=int(bool(state["todo"])))
        return True

    async def _current_card_page(self) -> bool:
        for offset in range(len(CARD_KINDS)):
            index = (self._card_turn + offset) % len(CARD_KINDS)
            kind = CARD_KINDS[index]
            scope = card_scope(kind)
            if self._due(scope, self.settings.analytics_reconcile_sweep_seconds):
                self._card_turn = (index + 1) % len(CARD_KINDS)
                return await self._card_page(scope, current_only=True, selected_kind=kind)
        return False

    def _due(self, scope: str, seconds: float) -> bool:
        row = self.progress.get(scope)
        if row is None or row.get("pending"):
            return True
        completed = _timestamp(row.get("completed_at"))
        return completed is None or (self.clock() - completed).total_seconds() >= seconds

    async def _card_page(
        self,
        scope: str,
        *,
        current_only: bool,
        fast: bool = False,
        selected_kind: str | None = None,
    ) -> bool:
        from devfeed_core.analytics_reconcile_sources import card_ids

        kinds = (selected_kind,) if selected_kind is not None else CARD_KINDS
        if selected_kind is not None and scope != card_scope(selected_kind):
            raise ValueError("Card scope does not match its population")
        state = self._state(scope)
        if "phase" not in state:
            state.update(
                phase="target" if fast else "source",
                index=0,
                after=None,
                started_at=self.clock().isoformat(),
            )
        if state["phase"] not in {"source", "target"} or not 0 <= state["index"] < len(kinds):
            raise AnalyticsUnavailable("Invalid current card sweep cursor")
        kind = kinds[state["index"]]
        started = _timestamp(state["started_at"])
        if started is None:
            raise AnalyticsUnavailable("Current card coverage boundary is missing")
        if state["phase"] == "source":
            async with self.connect() as connection, connection.transaction():
                await connection.execute("SET TRANSACTION READ ONLY")
                await connection.execute("SET LOCAL statement_timeout='5s'")
                identifiers = await card_ids(
                    connection,
                    kind,
                    state["after"],
                    self.batch,
                    current_only=current_only,
                    now=started,
                )
        else:
            bound = (
                " AND (shard>{after_shard:UInt16} OR "
                "(shard={after_shard:UInt16} AND entity_id>{after:UUID}))"
                if state["after"] is not None
                else ""
            )
            current = (
                " AND (has_active_work=1 OR state='running' OR "
                "(next_transition_at IS NOT NULL AND "
                "next_transition_at<={now:DateTime64(6,'UTC')}))"
                if fast
                else " AND (terminal_at IS NULL OR terminal_at>={recent:DateTime64(6,'UTC')})"
                if current_only
                else ""
            )
            from datetime import timedelta

            rows = await self.client.query(
                "SELECT shard,entity_id FROM analytics_card_canonical FINAL "
                "WHERE policy={policy:String} AND kind={kind:String} AND deleted=0"
                + current
                + bound
                + " ORDER BY shard,entity_id LIMIT {limit:UInt32}"
                + FINAL_GUARDS,
                {
                    "policy": self.policy,
                    "kind": kind,
                    "now": self.clock(),
                    "recent": started - timedelta(days=7),
                    "after": state["after"],
                    "after_shard": state.get("after_shard", 0),
                    "limit": self.batch,
                },
            )
            identifiers = [str(row["entity_id"]) for row in rows]
        await self._refresh(kind, identifiers)
        if len(identifiers) == self.batch:
            state["after"] = identifiers[-1]
            if state["phase"] == "target":
                state["after_shard"] = rows[-1]["shard"]
        else:
            state["index"] += 1
            state["after"] = None
            if state["index"] == len(kinds):
                if state["phase"] == "source":
                    state.update(phase="target", index=0)
                else:
                    # Completion describes the oldest boundary of the entire
                    # covered set, never the request/page which happened last.
                    await self._save(
                        scope,
                        {"todo": state.get("todo", []), "last_page_at": self.clock().isoformat()},
                        pending=(
                            int(
                                any(
                                    value.startswith(selected_kind + ":")
                                    for value in self._state("cards").get("todo", [])
                                )
                            )
                            if selected_kind is not None
                            else int(bool(state.get("todo")))
                        ),
                        source_at=started,
                        completed_at=self.clock(),
                    )
                    return True
        state["last_page_at"] = self.clock().isoformat()
        await self._save(scope, state, pending=1)
        return True

    def _busy_reason(self, error: TimeoutError | ReconciliationBusy) -> str | None:
        if isinstance(error, ReconciliationBusy):
            return error.reason_code
        if self._interrupted_page_builder is not None:
            # This page stopped while awaiting its shielded native builder.
            return "builder_busy"
        return None

    async def _step(self, scope: str) -> bool:
        if scope == "todo":
            return await self._todo()
        if scope == "cards":
            return await self._current_card_page()
        if scope == "audience-metadata":
            return await self._audience_metadata.once()
        if scope == "overview":
            return await self._overview.once()
        return await self._card_page(
            scope, current_only=scope != "sweep", fast=scope == "active-cards"
        )

    async def once(self) -> bool:
        """Progress one bounded page; untouched history stays in ClickHouse."""
        self._interrupted_page_builder = None
        try:
            async with asyncio.timeout(PAGE_SECONDS):
                if not await self._lead():
                    return False
                await self._load()
                # Complete a fixed queue snapshot before slow card/overview
                # builders can age its coverage. The completion-based cooldown
                # leaves the weighted turn intact for the remaining work.
                if self.queue_cycle is not None or self._due(
                    "queues", self.settings.analytics_reconcile_poll_seconds
                ):
                    await self._queue_step()
                    return True
                last_sweep = _timestamp(self._state("sweep").get("last_page_at"))
                due = {
                    "overview": self._overview.due,
                    "audience-metadata": self._audience_metadata.due,
                    "todo": bool(self._state("cards").get("todo")),
                    "active-cards": self._due(
                        "active-cards", self.settings.analytics_reconcile_poll_seconds
                    ),
                    "cards": any(
                        self._due(card_scope(kind), self.settings.analytics_reconcile_sweep_seconds)
                        for kind in CARD_KINDS
                    ),
                    "sweep": last_sweep is None
                    or (self.clock() - last_sweep).total_seconds()
                    >= self.settings.analytics_reconcile_sweep_seconds,
                }
                slots = (
                    "overview",
                    "todo",
                    "active-cards",
                    "overview",
                    "cards",
                    "todo",
                    "sweep",
                    "audience-metadata",
                )
                for offset in range(len(slots)):
                    index = (self._turn + offset) % len(slots)
                    scope = slots[index]
                    if due[scope]:
                        self._turn = (index + 1) % len(slots)
                        return await self._step(scope)
                return False
        except (TimeoutError, ReconciliationBusy) as error:
            reason = self._busy_reason(error)
            if reason is None:
                await self.close()
                raise
            # A private source admission or an existing builder can be busy
            # without invalidating the independent, fixed active-job snapshot.
            # Failed card work has not reached its cursor/fingerprint ACK.
            logger.info(
                "analytics_current_reconciliation_busy",
                extra={"reason_code": reason},
            )
            return False
        except BaseException:
            # A failed leader/snapshot is never resumed as complete coverage.
            # Card cursors are acknowledged per page; active-job snapshots are
            # small and must be reacquired after losing the coordinator session.
            await self.close()
            raise
