"""Current-state repair, truthful coverage and bounded scheduling contracts."""

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_core import analytics_direct_cards as cards
from devfeed_core import analytics_reconcile as reconcile
from devfeed_core import analytics_reconcile_sources as sources
from devfeed_core.analytics import AnalyticsUnavailable, ReconciliationBusy
from devfeed_core.config import Settings
from psycopg.errors import QueryCanceled

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def identifier(value):
    return str(uuid.UUID(int=value))


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, world):
        self.world = world

    @asynccontextmanager
    async def transaction(self):
        self.world.events.append("begin")
        try:
            yield
        finally:
            self.world.events.append("commit")

    async def execute(self, statement, parameters=None):
        text = statement if isinstance(statement, str) else statement.as_string(None)
        self.world.events.append(text)
        if self.world.execute_error and self.world.execute_error in text:
            raise AnalyticsUnavailable("Controlled PostgreSQL failure")
        if "pg_try_advisory_lock" in text:
            acquired = (
                self.world.leader if parameters == reconcile.RECONCILE_LOCK else self.world.publish
            )
            return Result([{"acquired": acquired}])
        if "SELECT pg_advisory_lock" in text:
            if not self.world.publish:
                raise QueryCanceled("private SQL or connection credentials")
            return Result()
        if "txid_current()" in text:
            if self.world.missing_fence:
                return Result()
            self.world.revision += 1
            return Result([{"revision": self.world.revision, "observed_at": self.world.now}])
        for table in reconcile.JOB_TABLES:
            if f'FROM "{table}" AS source' in text:
                self.world.source_reads.append((text, parameters))
                return Result(self.world.jobs.get(table, [])[: parameters[0]])
        return Result()


class Sink:
    def __init__(self, world):
        self.world = world
        self.settings = world.settings
        self.rows = {}
        self.writes = []
        self.queries = []
        self.fail_write = None
        self.fail_query = None
        self.after_ack = None

    def key(self, table, row):
        if table == reconcile.PROGRESS_TABLE:
            return row["policy"], row["scope"]
        if table == reconcile.FINGERPRINT_TABLE:
            return row["policy"], row["kind"], str(row["entity_id"])
        if table == "analytics_pipeline_events":
            return row["domain"], str(row["id"])
        return row["policy"], row["kind"], str(row["entity_id"])

    def put(self, table, rows):
        target = self.rows.setdefault(table, {})
        for row in deepcopy(rows):
            key = self.key(table, row)
            version = row.get("revision", row.get("version", 0))
            old = target.get(key, {})
            if version >= old.get("revision", old.get("version", 0)):
                target[key] = row

    async def insert(self, table, rows):
        self.world.events.append("ch:" + table)
        if self.fail_write == table:
            raise AnalyticsUnavailable("Controlled insert failure")
        self.writes.append((table, deepcopy(rows)))
        self.put(table, rows)
        if self.after_ack is not None:
            self.after_ack(table)

    async def query(self, statement, parameters=None, *, external_data=None):
        params = parameters or {}
        if external_data is not None:
            payload = external_data.files[0].data
            assert external_data.files[0].structure == "id UUID"
            assert len(payload) <= 16 * reconcile.MAX_ACTIVE_JOBS
            identities = {
                str(
                    uuid.UUID(
                        bytes=payload[start : start + 8][::-1]
                        + payload[start + 8 : start + 16][::-1]
                    )
                )
                for start in range(0, len(payload), 16)
            }
            params = {**params, "valid" if "NOT IN" in statement else "ids": identities}
        self.queries.append((statement, deepcopy(params)))
        if "FINAL" in statement:
            assert reconcile.FINAL_GUARDS in statement
        if self.fail_query is not None:
            raise self.fail_query
        self.world.events.append("ch:query")
        if "analytics_user_events" in statement:
            return []  # This current-card fixture has no audience observations.
        if reconcile.PROGRESS_TABLE in statement:
            rows = list(self.rows.get(reconcile.PROGRESS_TABLE, {}).values())
            return deepcopy(
                [
                    row
                    for row in rows
                    if row["policy"] == params["policy"]
                    and ("scopes" not in params or row["scope"] in params["scopes"])
                    and ("scope" not in params or row["scope"] == params["scope"])
                ]
            )
        if reconcile.FINGERPRINT_TABLE in statement:
            rows = [
                row
                for row in self.rows.get(reconcile.FINGERPRINT_TABLE, {}).values()
                if row["policy"] == params["policy"] and not row["deleted"]
            ]
            if "kinds" in params:
                rows = [row for row in rows if row["kind"] in params["kinds"]]
            else:
                rows = [
                    row
                    for row in rows
                    if row["kind"] == params["kind"] and str(row["entity_id"]) in params["ids"]
                ]
            return deepcopy(rows[: params.get("limit", len(rows))])
        if "analytics_pipeline_events" in statement:
            rows = [
                row
                for row in self.rows.get("analytics_pipeline_events", {}).values()
                if row["domain"] == "queue"
            ]
            if "ids" in params:
                return deepcopy([row for row in rows if str(row["id"]) in params["ids"]])
            rows = [
                row
                for row in rows
                if not row["deleted"]
                and row["status"] in {"queued", "running"}
                and row["revision"] <= params["fence"]
                and str(row["id"]) not in params["valid"]
                and (
                    params["cursor"] is None
                    or uuid.UUID(str(row["id"])) > uuid.UUID(params["cursor"])
                )
            ]
            return deepcopy(
                sorted(rows, key=lambda row: uuid.UUID(str(row["id"])))[: params["limit"]]
            )
        rows = [
            row
            for row in self.rows.get("analytics_card_canonical", {}).values()
            if row["policy"] == params["policy"] and row["kind"] == params["kind"]
        ]
        if "ids" in params:
            return deepcopy([row for row in rows if str(row["entity_id"]) in params["ids"]])
        rows = [row for row in rows if not row["deleted"]]
        if "has_active_work=1" in statement:
            rows = [
                row
                for row in rows
                if row["has_active_work"]
                or row["state"] == "running"
                or (
                    row["next_transition_at"] is not None
                    and row["next_transition_at"] <= params["now"]
                )
            ]
        elif "terminal_at IS NULL" in statement:
            rows = [
                row
                for row in rows
                if row["terminal_at"] is None or row["terminal_at"] >= params["recent"]
            ]
        if params["after"] is not None:
            rows = [
                row
                for row in rows
                if (row["shard"], uuid.UUID(str(row["entity_id"])))
                > (params["after_shard"], uuid.UUID(params["after"]))
            ]
        rows.sort(key=lambda row: (row["shard"], uuid.UUID(str(row["entity_id"]))))
        return deepcopy(rows[: params["limit"]])


class World:
    def __init__(self, monkeypatch, *, batch=2):
        self.now = NOW
        self.revision = 100
        self.events, self.source_reads = [], []
        self.jobs, self.entities = {}, {kind: {} for kind in reconcile.CARD_KINDS}
        self.leader = self.publish = True
        self.missing_fence = False
        self.execute_error = None
        self.opened = self.closed = self.builds = 0
        self.settings = Settings(
            database_url="postgresql+psycopg://unused/reconcile_test",
            analytics_reconcile_batch_size=batch,
        )
        self.sink = Sink(self)
        self.worker = reconcile.CurrentStateReconciler(
            self.sink, self.settings, self.connect, clock=lambda: self.now
        )
        # This suite isolates current queue/card repair; retained overview
        # recovery has its own source, lifecycle and native concurrency suite.
        self.worker._overview = SimpleNamespace(due=False)

        async def fingerprints(_connection, kind, identifiers):
            return {
                value: row.get("fingerprint", "v1")
                for value in identifiers
                if (row := self.entities[kind].get(value)) is not None
            }

        async def card_ids(_connection, kind, cursor, limit, *, current_only, now):
            return [
                value
                for value in sorted(self.entities[kind], key=uuid.UUID)
                if cursor is None or uuid.UUID(value) > uuid.UUID(cursor)
            ][:limit]

        def build(_settings, kind, identifiers, revision):
            self.builds += len(identifiers)
            return [
                cards.normalize_card(
                    self.entities[kind].get(value),
                    self.worker.policy,
                    kind,
                    uuid.UUID(value),
                    revision,
                    self.now,
                )
                for value in identifiers
            ]

        monkeypatch.setattr(sources, "card_fingerprints", fingerprints)
        monkeypatch.setattr(sources, "card_ids", card_ids)
        monkeypatch.setattr(cards, "build_cards", build)

    @asynccontextmanager
    async def connect(self):
        self.opened += 1
        try:
            yield Connection(self)
        finally:
            self.closed += 1
            self.events.append("closed")

    def entity(self, kind="article", value=1, **updates):
        identity = identifier(value)
        self.entities[kind][identity] = {
            "stage": "review",
            "state": "ready",
            "payload": {},
            "created_at": NOW,
            "activity_at": NOW,
            **updates,
        }
        return identity

    def job(self, value=1, *, status="queued", valid=True, table="article_enrichment_jobs"):
        record = {
            "id": identifier(value),
            "status": status,
            "created_at": NOW,
            "attempts": 1,
            "article_id": identifier(value),
            "source_id": None,
        }
        self.jobs.setdefault(table, []).append(
            {
                "record": record,
                "stamp": "1",
                "leased": valid,
                "lease_until": NOW + timedelta(seconds=60) if valid else NOW - timedelta(seconds=1),
            }
        )
        return reconcile._identity("queue", reconcile.JOB_KINDS[table], identifier(value))

    def queue(self, identity, *, revision=10, status="running", kind="article_enrichment"):
        row = reconcile._base("queue", identity, revision)
        row.update(status=status, task=kind, attempt=1, created_at=NOW)
        self.sink.put("analytics_pipeline_events", [row])


async def queue_cycle(world):
    await world.worker._load()
    for _ in range(100):
        await world.worker._queue_step()
        if world.worker.queue_cycle is None:
            return
    raise AssertionError("Queue cycle did not terminate")


def test_queue_repair_uses_valid_leases_and_preserves_attempt_history(monkeypatch):
    world = World(monkeypatch)
    valid = world.job(1, status="running")
    expired = world.job(2, status="running", valid=False)
    queued = world.job(3)
    ghost, newer = identifier(4), identifier(5)
    for identity in (valid, expired, queued, ghost):
        world.queue(identity)
    world.queue(newer, revision=1000)
    history = reconcile._base("job", identifier(6), 1)
    world.sink.put("analytics_pipeline_events", [history])
    asyncio.run(queue_cycle(world))
    rows = world.sink.rows["analytics_pipeline_events"]
    assert rows[("queue", valid)]["status"] == "running"
    assert rows[("queue", queued)]["status"] == "queued"
    assert rows[("queue", expired)]["deleted"] == rows[("queue", ghost)]["deleted"] == 1
    assert rows[("queue", newer)]["revision"] == 1000
    assert rows[("job", identifier(6))] == history
    assert all(
        row["domain"] == "queue"
        for table, values in world.sink.writes
        if table == "analytics_pipeline_events"
        for row in values
    )
    assert all("status IN ('queued','running')" in statement for statement, _ in world.source_reads)
    assert world.worker.progress["queues"]["pending"] == 0
    assert world.worker.progress["queues"]["source_at"] == NOW


def test_unchanged_job_snapshot_does_not_rewrite_or_requeue_owners(monkeypatch):
    world = World(monkeypatch)
    world.job()
    asyncio.run(queue_cycle(world))
    world.sink.writes.clear()
    asyncio.run(queue_cycle(world))
    assert not any(
        table in {"analytics_pipeline_events", reconcile.FINGERPRINT_TABLE}
        for table, _ in world.sink.writes
    )


def test_queue_fingerprints_keep_owner_refresh_durable_before_ack(monkeypatch):
    world = World(monkeypatch)
    world.job()
    world.sink.fail_write = reconcile.FINGERPRINT_TABLE
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(queue_cycle(world))
    pending = json.loads(world.worker.progress["cards"]["cursor"])["todo"]
    assert pending == ["article:" + identifier(1)]
    assert not world.sink.rows.get(reconcile.FINGERPRINT_TABLE)
    assert world.worker.progress["queues"]["pending"] == 1


def test_active_snapshot_overflow_cannot_authorize_absence(monkeypatch):
    world = World(monkeypatch)
    monkeypatch.setattr(reconcile, "MAX_ACTIVE_JOBS", 2)
    for value in range(3):
        world.job(value)
    world.queue(identifier(99))
    with pytest.raises(AnalyticsUnavailable, match="exceeds"):
        asyncio.run(queue_cycle(world))
    assert not world.sink.writes
    assert world.sink.rows["analytics_pipeline_events"][("queue", identifier(99))]["deleted"] == 0


def test_card_target_races_do_not_ack_fingerprints_and_late_overwrites_are_repaired(monkeypatch):
    world = World(monkeypatch)
    identity = world.entity()

    def stale_writer(table):
        if table == "analytics_card_canonical":
            world.revision += 10
            key = (world.worker.policy, "article", identity)
            row = world.sink.rows[table][key]
            row.update(version=world.revision << 64, semantic_hash="stale")

    world.sink.after_ack = stale_writer
    with pytest.raises(AnalyticsUnavailable, match="changed"):
        asyncio.run(world.worker._refresh("article", [identity]))
    assert not world.sink.rows.get(reconcile.FINGERPRINT_TABLE)
    world.sink.after_ack = None
    asyncio.run(world.worker._refresh("article", [identity]))
    builds = world.builds
    asyncio.run(world.worker._refresh("article", [identity]))
    assert world.builds == builds
    key = (world.worker.policy, "article", identity)
    world.sink.rows["analytics_card_canonical"][key]["semantic_hash"] = "late-stale"
    asyncio.run(world.worker._refresh("article", [identity]))
    assert world.builds == builds + 1
    assert world.sink.rows["analytics_card_canonical"][key]["semantic_hash"] != "late-stale"


def test_missing_cards_tombstone_without_creating_job_facts_and_volatile_inputs_rebuild(
    monkeypatch,
):
    world = World(monkeypatch)
    present = world.entity(fingerprint="")
    missing = identifier(2)
    asyncio.run(world.worker._refresh("article", [present, missing]))
    asyncio.run(world.worker._refresh("article", [present, missing]))
    assert world.builds == 3
    assert (
        world.sink.rows["analytics_card_canonical"][(world.worker.policy, "article", missing)][
            "deleted"
        ]
        == 1
    )
    assert not any(table == "analytics_pipeline_events" for table, _ in world.sink.writes)


def test_card_coverage_advances_only_after_complete_source_and_reverse_target_pass(monkeypatch):
    world = World(monkeypatch, batch=1)
    world.entity(value=0)

    async def scenario():
        await world.worker._load()
        await world.worker._card_page(
            reconcile.card_scope("article"), current_only=True, selected_kind="article"
        )
        assert world.worker.progress[reconcile.card_scope("article")]["completed_at"] is None
        assert world.worker.progress[reconcile.card_scope("article")]["pending"] == 1
        for _ in range(30):
            world.now += timedelta(seconds=1)
            await world.worker._card_page(
                reconcile.card_scope("article"), current_only=True, selected_kind="article"
            )
            if not world.worker.progress[reconcile.card_scope("article")]["pending"]:
                break
        else:
            pytest.fail("Covered current-card pass never completed")

    asyncio.run(scenario())
    assert world.worker.progress[reconcile.card_scope("article")]["source_at"] == NOW
    assert world.worker.progress[reconcile.card_scope("article")]["completed_at"] > NOW
    assert world.builds == 1


def test_fence_precedes_snapshot_and_publish_lock_is_released_before_source_reads(monkeypatch):
    world = World(monkeypatch)
    world.job()
    asyncio.run(world.worker._snapshot())
    events = world.events
    lock = next(i for i, text in enumerate(events) if "SELECT pg_advisory_lock" in text)
    begin = events.index("begin")
    fence = next(i for i, text in enumerate(events) if "txid_current" in text)
    unlock = next(i for i, text in enumerate(events) if "pg_advisory_unlock" in text)
    source = next(i for i, text in enumerate(events) if "AS record" in text)
    admission_timeout = events.index("SET statement_timeout='10s'")
    source_timeout = events.index("SET statement_timeout='5s'")
    assert admission_timeout < lock < source_timeout < begin < fence < unlock < source
    assert source < events.index("commit")
    assert events.index("closed") < events.index("ch:query")
    assert any("REPEATABLE READ READ ONLY" in text for text in events)


def test_bounded_weighted_scheduler_progresses_all_due_work(monkeypatch):
    world = World(monkeypatch)
    selected = []

    async def lead():
        return True

    async def load():
        return None

    async def queue():
        selected.append("queues")
        await world.worker._save("queues", {}, pending=0, source_at=NOW, completed_at=NOW)

    async def todo():
        selected.append("todo")
        return True

    async def page(scope, **_options):
        selected.append(scope)
        return True

    async def overview():
        selected.append("overview")
        return True

    async def audience():
        selected.append("audience-metadata")
        return True

    monkeypatch.setattr(world.worker, "_lead", lead)
    monkeypatch.setattr(world.worker, "_load", load)
    monkeypatch.setattr(world.worker, "_queue_step", queue)
    monkeypatch.setattr(world.worker, "_todo", todo)
    monkeypatch.setattr(world.worker, "_card_page", page)
    world.worker._overview = SimpleNamespace(due=True, once=overview)
    world.worker._audience_metadata = SimpleNamespace(due=True, once=audience)
    monkeypatch.setattr(
        world.worker,
        "_state",
        lambda scope: {"todo": ["article:" + identifier(1)]} if scope == "cards" else {},
    )

    async def scenario():
        for _ in range(9):
            assert await world.worker.once()

    asyncio.run(scenario())
    assert selected == [
        "queues",
        "overview",
        "todo",
        "active-cards",
        "overview",
        "cards:article",
        "todo",
        "sweep",
        "audience-metadata",
    ]


@pytest.mark.parametrize("turn", [0, 4, 7])
def test_queue_snapshot_finishes_before_slow_pages_and_due_poll_resumes_their_turn(
    monkeypatch, turn
):
    world = World(monkeypatch, batch=2)
    for value in range(5):
        world.job(value)
    world.worker._turn = turn
    selected = []
    original_insert = world.sink.insert

    async def acknowledged_insert(table, rows):
        await original_insert(table, rows)
        world.now += timedelta(milliseconds=100)

    async def slow_page(scope, **_options):
        selected.append(scope)
        world.now += timedelta(seconds=20)
        return True

    async def todo():
        return await slow_page("todo")

    async def overview():
        return await slow_page("overview")

    async def audience():
        return await slow_page("audience-metadata")

    monkeypatch.setattr(world.sink, "insert", acknowledged_insert)
    monkeypatch.setattr(world.worker, "_card_page", slow_page)
    monkeypatch.setattr(world.worker, "_todo", todo)
    world.worker._overview = SimpleNamespace(due=True, once=overview)
    world.worker._audience_metadata = SimpleNamespace(due=True, once=audience)

    async def complete_queue():
        for _ in range(50):
            assert await world.worker.once()
            if world.worker.queue_cycle is None and "queues" in world.worker.progress:
                return deepcopy(world.worker.progress["queues"])
            if world.worker.queue_cycle is not None:
                assert world.worker.progress["queues"]["pending"]
        pytest.fail("Bounded queue snapshot did not complete")

    async def scenario():
        first = await complete_queue()
        assert selected == [] and world.worker._turn == turn
        assert first["source_at"] == NOW
        assert first["completed_at"] - first["source_at"] < timedelta(seconds=5)
        assert not first["pending"]
        for _ in range(2):
            assert await world.worker.once()
            assert world.worker.queue_cycle is None
        assert len(selected) == 2
        resumed_turn = world.worker._turn
        second = await complete_queue()
        assert len(selected) == 2 and world.worker._turn == resumed_turn
        assert second["source_at"] > first["completed_at"] + timedelta(seconds=30)
        assert second["completed_at"] - second["source_at"] < timedelta(seconds=5)
        assert not second["pending"]
        assert await world.worker.once()
        assert len(selected) == 3
        await world.worker.close()

    asyncio.run(scenario())
    assert world.opened == world.closed


def test_leader_loss_discards_uncovered_snapshot_and_releases_connection(monkeypatch):
    world = World(monkeypatch)
    world.sink.fail_query = AnalyticsUnavailable("Controlled connection loss")
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(world.worker.once())
    assert world.worker._leader is None and world.worker.queue_cycle is None
    assert world.opened == world.closed


@pytest.mark.parametrize(
    "failed_table",
    [reconcile.PROGRESS_TABLE, "analytics_pipeline_events", reconcile.FINGERPRINT_TABLE],
)
def test_priority_queue_page_failure_preserves_old_coverage_until_retry_ack(
    monkeypatch, failed_table
):
    world = World(monkeypatch)
    world.job()
    before = NOW - timedelta(minutes=2)

    async def scenario():
        await world.worker._save("queues", {}, pending=0, source_at=before, completed_at=before)
        world.sink.fail_write = failed_table
        with pytest.raises(AnalyticsUnavailable):
            await world.worker.once()
        assert world.worker.queue_cycle is None and world.worker._leader is None
        assert world.worker.progress["queues"]["source_at"] == before
        assert world.worker.progress["queues"]["completed_at"] == before
        assert world.builds == 0 and world.worker._turn == 0
        world.sink.fail_write = None
        for _ in range(3):
            assert await world.worker.once()
        assert world.worker.queue_cycle is None
        assert not world.worker.progress["queues"]["pending"]
        assert world.worker.progress["queues"]["source_at"] == NOW
        await world.worker.close()

    asyncio.run(scenario())
    assert world.opened == world.closed


@pytest.mark.parametrize("busy", ["leader", "publisher"])
def test_busy_coordinator_or_publisher_cannot_claim_coverage(monkeypatch, busy):
    world = World(monkeypatch)
    setattr(world, "leader" if busy == "leader" else "publish", False)
    assert not asyncio.run(world.worker.once())
    assert not world.sink.writes
    asyncio.run(world.worker.close())
    assert world.opened == world.closed


def test_status_is_pending_for_missing_checkpoint_or_only_unknown_table(monkeypatch):
    world = World(monkeypatch)
    world.sink.settings = world.settings.model_copy(
        update={"analytics_reconcile_poll_seconds": 31.5}
    )
    status = asyncio.run(reconcile.read_reconciliation_status(world.sink, world.worker.policy))
    assert status["queues"] == {
        "completed_at": None,
        "source_at": None,
        "pending": True,
        "max_age_seconds": 63,
    }
    assert status["cards"]["max_age_seconds"] == 600
    missing = AnalyticsUnavailable("Controlled driver failure")
    missing.__cause__ = RuntimeError("Code: 60. UNKNOWN_TABLE")
    world.sink.fail_query = missing
    assert (
        asyncio.run(reconcile.read_reconciliation_status(world.sink, world.worker.policy)) == status
    )
    world.sink.fail_query = AnalyticsUnavailable("Offline")
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(reconcile.read_reconciliation_status(world.sink, world.worker.policy))


def test_status_rejects_ambiguous_or_invalid_coverage(monkeypatch):
    world = World(monkeypatch)
    row = {
        "policy": world.worker.policy,
        "scope": reconcile.card_scope("article"),
        "pending": 0,
        "version": 1,
        "completed_at": NOW.isoformat(),
        "source_at": NOW.isoformat(),
    }
    world.sink.put(reconcile.PROGRESS_TABLE, [row])
    status = asyncio.run(
        reconcile.read_reconciliation_status(
            world.sink, world.worker.policy, card_kinds=("article",)
        )
    )
    assert status["cards"]["source_at"] == NOW and not status["cards"]["pending"]

    async def duplicates(*_args):
        return [row, row]

    monkeypatch.setattr(world.sink, "query", duplicates)
    with pytest.raises(ValueError, match="Ambiguous"):
        asyncio.run(
            reconcile.read_reconciliation_status(
                world.sink, world.worker.policy, card_kinds=("article",)
            )
        )


@pytest.mark.parametrize("value", [NOW, NOW.replace(tzinfo=None), NOW.isoformat(), None])
def test_coverage_timestamps_are_utc(monkeypatch, value):
    assert reconcile._timestamp(value) == (NOW if value is not None else None)


def test_invalid_timestamps_and_cursors_fail_closed(monkeypatch):
    world = World(monkeypatch)
    with pytest.raises(ValueError):
        reconcile._timestamp(1)
    for raw in ("[]", '{"todo":["bad"]}', '{"todo":["unknown:bad"]}'):
        world.worker.progress["cards"] = {"cursor": raw}
        with pytest.raises(AnalyticsUnavailable):
            world.worker._state("cards")


def test_full_worker_lifecycle_repairs_then_idles_without_starving_pending_cards(monkeypatch):
    world = World(monkeypatch)
    world.entity()
    world.job()

    async def scenario():
        for _ in range(80):
            worked = await world.worker.once()
            if not worked:
                break
        else:
            pytest.fail("Covered current state did not become idle")
        assert not await world.worker._todo()
        assert not world.worker.progress[reconcile.card_scope("article")]["pending"]
        assert not world.worker.progress["queues"]["pending"]
        assert world.worker.progress["sweep"]["pending"]  # Archival pages are rate bounded.
        await world.worker.close()

    asyncio.run(scenario())
    assert world.builds == 1
    assert world.opened == world.closed


def test_reparent_and_terminal_transition_refresh_both_owners_without_rebuilding_history(
    monkeypatch,
):
    world = World(monkeypatch)
    world.job()
    asyncio.run(queue_cycle(world))
    record = world.jobs["article_enrichment_jobs"][0]
    record["stamp"] = "2"
    record["record"]["article_id"] = identifier(2)
    world.sink.writes.clear()
    asyncio.run(queue_cycle(world))
    todo = json.loads(world.worker.progress["cards"]["cursor"])["todo"]
    assert todo == ["article:" + identifier(1), "article:" + identifier(2)]
    assert not any(table == "analytics_pipeline_events" for table, _ in world.sink.writes)
    world.jobs.clear()
    asyncio.run(queue_cycle(world))
    assert all(row["deleted"] for row in world.sink.rows[reconcile.FINGERPRINT_TABLE].values())


def test_unchanged_fingerprint_still_repairs_an_overwritten_queue_target(monkeypatch):
    world = World(monkeypatch)
    identity = world.job()
    asyncio.run(queue_cycle(world))
    world.sink.rows["analytics_pipeline_events"][("queue", identity)]["status"] = "running"
    world.sink.writes.clear()
    asyncio.run(queue_cycle(world))
    assert world.sink.rows["analytics_pipeline_events"][("queue", identity)]["status"] == "queued"
    assert not any(table == reconcile.FINGERPRINT_TABLE for table, _ in world.sink.writes)


def test_unknown_queue_ids_are_tombstoned_in_bounded_native_cursor_pages(monkeypatch):
    world = World(monkeypatch, batch=2)
    for value in range(5):
        world.queue(identifier(value))
    asyncio.run(queue_cycle(world))
    assert all(row["deleted"] for row in world.sink.rows["analytics_pipeline_events"].values())
    cleanup = [params for text, params in world.sink.queries if "fence" in params]
    assert cleanup[0]["cursor"] is None
    assert any(params["cursor"] is not None for params in cleanup[1:])
    assert all(params["limit"] == 2 for params in cleanup)


def test_queue_identifier_overflow_is_rejected_before_transport():
    with pytest.raises(AnalyticsUnavailable, match="identifier set exceeds its bound"):
        reconcile._queue_identifiers([identifier(1)] * (reconcile.MAX_ACTIVE_JOBS + 1))


def test_fingerprint_and_owner_target_overflow_leave_existing_views_unmodified(monkeypatch):
    world = World(monkeypatch)
    monkeypatch.setattr(reconcile, "MAX_ACTIVE_JOBS", 2)
    for value in range(3):
        world.sink.put(
            reconcile.FINGERPRINT_TABLE,
            [
                {
                    "policy": world.worker.policy,
                    "kind": "queue:ingestion",
                    "entity_id": identifier(value),
                    "version": 1,
                    "fingerprint": "old",
                    "target_hash": "old",
                    "owners": [],
                    "deleted": 0,
                }
            ],
        )
    with pytest.raises(AnalyticsUnavailable, match="fingerprint set"):
        asyncio.run(world.worker._snapshot())
    with pytest.raises(AnalyticsUnavailable, match="target set"):
        asyncio.run(world.worker._enqueue(["article:" + identifier(value) for value in range(3)]))
    assert not world.sink.writes


@pytest.mark.parametrize("failure", ["leader", "fence", "source"])
def test_failed_coordination_and_source_reads_release_every_lock_connection(monkeypatch, failure):
    world = World(monkeypatch)
    if failure == "leader":
        world.execute_error = "SET statement_timeout"
    elif failure == "fence":
        world.missing_fence = True
    else:
        world.execute_error = "AS record"
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(world.worker.once())
    assert world.opened == world.closed
    assert not world.sink.writes
    if failure == "fence":
        assert any("pg_advisory_unlock" in text for text in world.events)


def test_missing_lease_is_not_published_as_running(monkeypatch):
    world = World(monkeypatch)
    identity = world.job(status="running")
    world.jobs["article_enrichment_jobs"][0]["lease_until"] = None
    asyncio.run(queue_cycle(world))
    assert world.sink.rows["analytics_pipeline_events"][("queue", identity)]["deleted"] == 1


def test_incomplete_builder_and_missing_ack_target_never_cache_source_fingerprints(monkeypatch):
    world = World(monkeypatch)
    identity = world.entity()
    original = cards.build_cards
    monkeypatch.setattr(cards, "build_cards", lambda *_args: [])
    with pytest.raises(AnalyticsUnavailable, match="Incomplete"):
        asyncio.run(world.worker._refresh("article", [identity]))
    assert not world.sink.writes
    monkeypatch.setattr(cards, "build_cards", original)

    def remove_after_write(table):
        if table == "analytics_card_canonical":
            world.sink.rows[table].clear()

    world.sink.after_ack = remove_after_write
    with pytest.raises(AnalyticsUnavailable, match="changed"):
        asyncio.run(world.worker._refresh("article", [identity]))
    assert not world.sink.rows.get(reconcile.FINGERPRINT_TABLE)


def test_busy_publication_and_empty_card_batches_do_not_claim_coverage(monkeypatch):
    world = World(monkeypatch)
    assert asyncio.run(world.worker._card_targets("article", [])) == {}
    assert not asyncio.run(world.worker._refresh("article", []))
    world.publish = False
    with pytest.raises(ReconciliationBusy, match="busy") as caught:
        asyncio.run(world.worker._refresh("article", [identifier(1)]))
    assert caught.value.reason_code == "publication_busy"
    assert not world.sink.writes


@pytest.mark.parametrize("reason", ["publication_busy", "builder_busy"])
def test_queue_cycle_finishes_before_busy_card_page_without_acknowledging_cards(
    monkeypatch, caplog, reason
):
    world = World(monkeypatch, batch=1)
    for value in (1, 2):
        world.entity(value=value)
        world.job(value)

    async def scenario():
        await world.worker._lead()
        await world.worker._load()
        await world.worker._queue_step()
        cycle = world.worker.queue_cycle
        assert cycle is not None and cycle.position == 1
        builder = None
        try:
            if reason == "publication_busy":
                world.publish = False
            else:
                builder = asyncio.create_task(asyncio.sleep(60))
                world.worker._builder = builder
            world.worker._turn = 1  # The pending owner card waits for queue coverage.
            for _ in range(3):
                assert await world.worker.once()
            assert world.worker.queue_cycle is None
            assert not world.worker.progress["queues"]["pending"]
            before = deepcopy(world.sink.writes)
            coverage = deepcopy(world.worker.progress["queues"])
            todo = deepcopy(world.worker._state("cards")["todo"])
            assert not await world.worker.once()
            assert world.worker.queue_cycle is None
            assert world.worker.progress["queues"] == coverage
            assert world.worker._leader is not None
            assert world.worker._state("cards")["todo"] == todo
            assert world.sink.writes == before
            assert not world.sink.rows.get("analytics_card_canonical")
            assert not any(
                row["kind"].startswith("card:")
                for row in world.sink.rows[reconcile.FINGERPRINT_TABLE].values()
            )
            world.publish = True
        finally:
            if builder is not None:
                builder.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await builder
            await world.worker.close()

    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    record = next(
        record
        for record in caplog.records
        if record.message == "analytics_current_reconciliation_busy"
    )
    assert record.reason_code == reason
    assert record.exc_info is None
    assert "private SQL or connection credentials" not in caplog.text
    assert world.opened == world.closed


def test_unrelated_queue_timeout_is_not_hidden_by_a_previous_pending_builder(monkeypatch):
    world = World(monkeypatch, batch=1)
    world.job(1)
    world.job(2)
    monkeypatch.setattr(reconcile, "PAGE_SECONDS", 0.01)

    async def stalled_queue():
        await asyncio.sleep(60)

    async def scenario():
        await world.worker._lead()
        await world.worker._load()
        await world.worker._queue_step()
        assert world.worker.queue_cycle is not None
        builder = asyncio.create_task(asyncio.sleep(60))
        world.worker._builder = builder
        # Even an old interrupted page marker must not classify this new wait.
        world.worker._interrupted_page_builder = builder
        monkeypatch.setattr(world.worker, "_queue_step", stalled_queue)
        try:
            with pytest.raises(TimeoutError):
                await world.worker.once()
            assert world.worker.queue_cycle is None
            assert world.worker._leader is None
            assert not builder.done()
        finally:
            builder.cancel()
            with pytest.raises(asyncio.CancelledError):
                await builder
            await world.worker.close()

    asyncio.run(scenario())
    assert world.opened == world.closed


@pytest.mark.parametrize("cancelled", [False, True])
def test_interrupted_own_builder_never_acknowledges_cards_or_changes_queue_coverage(
    monkeypatch, cancelled
):
    world = World(monkeypatch, batch=1)
    for value in (1, 2):
        world.entity(value=value)
        world.job(value)
    original = cards.build_cards
    started, release = threading.Event(), threading.Event()

    def stalled_builder(*args):
        started.set()
        if not release.wait(5):
            raise AssertionError("Test did not release its native builder")
        return original(*args)

    monkeypatch.setattr(cards, "build_cards", stalled_builder)
    monkeypatch.setattr(reconcile, "PAGE_SECONDS", 1 if cancelled else 0.05)

    async def scenario():
        await world.worker._lead()
        await queue_cycle(world)
        coverage = deepcopy(world.worker.progress["queues"])
        before = deepcopy(world.sink.writes)
        todo = deepcopy(world.worker._state("cards")["todo"])
        world.worker._turn = 1
        task = asyncio.create_task(world.worker.once())
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set()
            if cancelled:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert world.worker.queue_cycle is None
                assert world.worker._leader is None
            else:
                assert not await task
                assert world.worker.queue_cycle is None
                assert world.worker._leader is not None
            assert world.worker.progress["queues"] == coverage
            assert world.worker._state("cards")["todo"] == todo
            assert world.sink.writes == before
            assert not world.sink.rows.get("analytics_card_canonical")
            release.set()
            await world.worker._builder
            assert world.sink.writes == before  # Abandoned native result stays unacknowledged.
            monkeypatch.setattr(cards, "build_cards", original)
            await world.worker._refresh("article", [identifier(1)])
            assert world.sink.rows["analytics_card_canonical"]
        finally:
            release.set()
            await world.worker.close()

    asyncio.run(scenario())
    assert world.opened == world.closed


def test_due_transitions_refresh_even_when_source_version_is_unchanged(monkeypatch):
    world = World(monkeypatch)
    identity = world.entity(state="retry_wait", next_transition_at=NOW - timedelta(seconds=1))
    asyncio.run(world.worker._refresh("article", [identity]))
    count = world.builds
    asyncio.run(world.worker._refresh("article", [identity]))
    assert world.builds == count + 1


def test_current_card_cursor_resumes_after_process_restart_and_ack_loss(monkeypatch):
    world = World(monkeypatch, batch=1)
    first, second = world.entity(value=0), world.entity(value=1 << 127)
    failed = False

    def uncertain_ack(table):
        nonlocal failed
        if table == reconcile.PROGRESS_TABLE and not failed:
            failed = True
            raise AnalyticsUnavailable("Acknowledgement lost after durable write")

    world.sink.after_ack = uncertain_ack
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(
            world.worker._card_page(
                reconcile.card_scope("article"), current_only=True, selected_kind="article"
            )
        )
    world.sink.after_ack = None
    world.worker = reconcile.CurrentStateReconciler(
        world.sink, world.settings, world.connect, clock=lambda: world.now
    )

    async def scenario():
        await world.worker._load()
        for _ in range(30):
            await world.worker._card_page(
                reconcile.card_scope("article"), current_only=True, selected_kind="article"
            )
            if not world.worker.progress[reconcile.card_scope("article")]["pending"]:
                break
        else:
            pytest.fail("Restarted coverage cursor did not complete")

    asyncio.run(scenario())
    assert world.builds == 2
    assert all(
        (world.worker.policy, "article", identity) in world.sink.rows["analytics_card_canonical"]
        for identity in (first, second)
    )
    assert any("shard>{after_shard:UInt16}" in statement for statement, _ in world.sink.queries)


def test_fast_card_scan_and_background_identity_page_keep_separate_coverage(monkeypatch):
    world = World(monkeypatch)
    identity = world.entity(has_active_work=True)
    asyncio.run(world.worker._refresh("article", [identity]))

    async def scenario():
        for _ in range(5):
            await world.worker._card_page("active-cards", current_only=True, fast=True)
        assert world.worker.progress["active-cards"]["completed_at"] == NOW
        assert "cards" not in world.worker.progress
        await world.worker._card_page("sweep", current_only=False)
        assert "cards" not in world.worker.progress

    asyncio.run(scenario())
    assert world.builds == 1


@pytest.mark.parametrize(
    "state",
    [
        {"phase": "unknown", "index": 0},
        {"phase": "source", "index": 5},
        {"phase": "source", "index": 0, "started_at": None},
    ],
)
def test_invalid_sweep_metadata_cannot_advance_coverage(monkeypatch, state):
    world = World(monkeypatch)
    world.worker.progress[reconcile.card_scope("article")] = {"cursor": json.dumps(state)}
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(
            world.worker._card_page(
                reconcile.card_scope("article"), current_only=True, selected_kind="article"
            )
        )
    assert not world.sink.writes


def test_duplicate_progress_and_card_targets_fail_closed(monkeypatch):
    world = World(monkeypatch)

    async def progress(*_args):
        return [{"scope": "cards"}, {"scope": "cards"}]

    monkeypatch.setattr(world.sink, "query", progress)
    with pytest.raises(AnalyticsUnavailable, match="Ambiguous"):
        asyncio.run(world.worker._load())

    async def targets(*_args):
        return [{"entity_id": identifier(1)}, {"entity_id": identifier(1)}]

    monkeypatch.setattr(world.sink, "query", targets)
    with pytest.raises(AnalyticsUnavailable, match="Ambiguous"):
        asyncio.run(world.worker._card_targets("article", [identifier(1)]))


def test_completed_owner_fingerprints_are_retired_in_multiple_acknowledged_pages(monkeypatch):
    world = World(monkeypatch, batch=2)
    for value in range(3):
        world.job(value)
    asyncio.run(queue_cycle(world))
    world.jobs.clear()
    asyncio.run(queue_cycle(world))
    assert all(row["deleted"] for row in world.sink.rows[reconcile.FINGERPRINT_TABLE].values())
    assert all(row["deleted"] for row in world.sink.rows["analytics_pipeline_events"].values())
    assert not world.worker.progress["queues"]["pending"]


@pytest.mark.parametrize("failed", [False, True])
def test_cancelled_native_builder_cannot_overlap_retries_or_ack_abandoned_results(
    monkeypatch, caplog, failed
):
    world = World(monkeypatch)
    identity = world.entity()
    original = cards.build_cards
    started, released = threading.Event(), threading.Event()
    calls = []

    def slow_build(*args):
        calls.append(args)
        started.set()
        if not released.wait(5):
            raise AssertionError("Test did not release its builder")
        if failed:
            raise ValueError("private SQL/password/body must never be logged")
        return original(*args)

    monkeypatch.setattr(cards, "build_cards", slow_build)

    async def scenario():
        await world.worker._lead()
        work = asyncio.create_task(world.worker._refresh("article", [identity]))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
            await asyncio.wait_for(world.worker.close(), timeout=0.5)
            with pytest.raises(AnalyticsUnavailable, match="still running"):
                await world.worker._refresh("article", [identity])
            assert len(calls) == 1 and not world.sink.writes
            released.set()
            if failed:
                with pytest.raises(ValueError):
                    await world.worker._builder
            else:
                await world.worker._builder
            monkeypatch.setattr(cards, "build_cards", original)
            await world.worker._refresh("article", [identity])
        finally:
            released.set()

    asyncio.run(scenario())
    assert world.opened == world.closed
    assert (
        world.sink.rows["analytics_card_canonical"][(world.worker.policy, "article", identity)][
            "deleted"
        ]
        == 0
    )
    assert "private SQL/password/body" not in caplog.text
    if failed:
        event = next(
            record
            for record in caplog.records
            if record.message == "analytics_current_builder_failed"
        )
        assert event.error_type == "ValueError"


def test_cancelled_builder_observer_does_not_inspect_a_missing_result():
    async def scenario():
        task = asyncio.create_task(asyncio.sleep(10))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reconcile._finished_builder(task)

    asyncio.run(scenario())


def test_small_populations_finish_independently_of_a_production_sized_article_scan(monkeypatch):
    world = World(monkeypatch, batch=100)
    # Production current populations: 148 article pages versus fourteen source pages.
    for value in range(14_807):
        world.entity(value=value)
    for value in range(1_321):
        world.entity(kind="source", value=value)
    for value in range(1_276):
        world.entity(kind="relationship-proposal", value=value)
    world.entity(kind="candidate")

    async def scenario():
        for _ in range(148):
            assert await world.worker._current_card_page()
            world.now += timedelta(seconds=2)
            if all(
                world.worker.progress.get(reconcile.card_scope(kind), {}).get("completed_at")
                for kind in ("source", "candidate")
            ):
                break
        else:
            pytest.fail("Small board populations waited behind the full article population")
        assert world.worker.progress[reconcile.card_scope("article")]["pending"]
        covered = await reconcile.read_reconciliation_status(
            world.sink, world.worker.policy, card_kinds=("source", "candidate")
        )
        assert not covered["cards"]["pending"]
        # Each population reserves its own boundary when it first gets a turn.
        assert covered["cards"]["source_at"] == NOW + timedelta(seconds=2)
        assert covered["cards"]["completed_at"] > covered["cards"]["source_at"]
        assert "cards" not in world.worker.progress
        assert "cards" not in {
            row["scope"] for row in world.sink.rows[reconcile.PROGRESS_TABLE].values()
        }

    asyncio.run(scenario())


def test_kind_target_failure_and_restart_preserve_tombstones_and_unacknowledged_coverage(
    monkeypatch,
):
    world = World(monkeypatch)
    present = world.entity(kind="source")
    ghost = identifier(2)
    world.sink.put(
        "analytics_card_canonical",
        [
            cards.normalize_card(
                {"stage": "active", "state": "ready"},
                world.worker.policy,
                "source",
                uuid.UUID(ghost),
                1,
                NOW,
            )
        ],
    )
    scope = reconcile.card_scope("source")

    async def scenario():
        await world.worker._card_page(scope, current_only=True, selected_kind="source")
        assert world.worker._state(scope)["phase"] == "target"
        assert world.worker.progress[scope]["source_at"] is None
        world.sink.fail_write = reconcile.PROGRESS_TABLE
        with pytest.raises(AnalyticsUnavailable):
            await world.worker._card_page(scope, current_only=True, selected_kind="source")
        assert world.worker.progress[scope]["completed_at"] is None
        assert (
            world.sink.rows["analytics_card_canonical"][(world.worker.policy, "source", ghost)][
                "deleted"
            ]
            == 1
        )
        world.sink.fail_write = None
        world.worker = reconcile.CurrentStateReconciler(
            world.sink, world.settings, world.connect, clock=lambda: world.now
        )
        await world.worker._load()
        for _ in range(10):
            await world.worker._card_page(scope, current_only=True, selected_kind="source")
            if not world.worker.progress[scope]["pending"]:
                break
        else:
            pytest.fail("Independent source coverage did not finish after restart")
        assert world.worker.progress[scope]["source_at"] == NOW
        assert (
            world.sink.rows["analytics_card_canonical"][(world.worker.policy, "source", present)][
                "deleted"
            ]
            == 0
        )

    asyncio.run(scenario())


def test_old_global_or_active_coverage_cannot_certify_missing_independent_populations(monkeypatch):
    world = World(monkeypatch)
    before = [
        {
            "policy": world.worker.policy,
            "scope": scope,
            "version": 99,
            "source_at": NOW,
            "completed_at": NOW,
            "pending": 0,
            "cursor": "{}",
        }
        for scope in ("cards", "active-cards")
    ]
    world.sink.put(reconcile.PROGRESS_TABLE, before)

    async def scenario():
        await world.worker._load()
        assert world.worker.progress["cards"]["source_at"] == NOW
        status = await reconcile.read_reconciliation_status(
            world.sink, world.worker.policy, card_kinds=("source", "candidate")
        )
        assert status["cards"]["pending"]
        assert status["cards"]["source_at"] is None
        assert status["cards"]["completed_at"] is None
        assert (
            world.sink.rows[reconcile.PROGRESS_TABLE][(world.worker.policy, "cards")] == before[0]
        )

    asyncio.run(scenario())


def test_dirty_owners_invalidate_only_their_populations_and_todo_turns_are_fair(monkeypatch):
    world = World(monkeypatch, batch=1)
    refreshed = []

    async def refresh(kind, identifiers):
        refreshed.append((kind, identifiers))
        return True

    monkeypatch.setattr(world.worker, "_refresh", refresh)

    async def scenario():
        for kind in reconcile.CARD_KINDS:
            await world.worker._save(
                reconcile.card_scope(kind), {}, source_at=NOW, completed_at=NOW, pending=0
            )
        await world.worker._enqueue(
            ["article:" + identifier(1), "article:" + identifier(2), "source:" + identifier(3)]
        )
        assert all(
            world.worker.progress[reconcile.card_scope(kind)]["pending"]
            == int(kind in {"article", "source"})
            for kind in reconcile.CARD_KINDS
        )
        for _ in range(3):
            assert await world.worker._todo()
        assert [kind for kind, _ in refreshed] == ["article", "source", "article"]
        assert not await world.worker._todo()
        # Refreshing dirty identities does not certify a missing-population or tombstone pass.
        assert world.worker.progress[reconcile.card_scope("source")]["pending"]
        assert world.worker.progress[reconcile.card_scope("source")]["source_at"] == NOW

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["second_kind", "ledger_ack", "fingerprint_ack"])
def test_partial_owner_enqueue_replays_after_restart_without_certifying_dirty_cards(
    monkeypatch, failure
):
    world = World(monkeypatch)
    article = world.entity(value=101)
    source = world.entity(kind="source", value=102)
    world.job(101)
    world.job(102, table="ingestion_jobs")
    record = world.jobs["ingestion_jobs"][0]["record"]
    record["article_id"], record["source_id"] = None, source
    owners = sorted(["article:" + article, "source:" + source])

    async def scenario():
        for kind in reconcile.CARD_KINDS:
            await world.worker._save(
                reconcile.card_scope(kind), {}, source_at=NOW, completed_at=NOW, pending=0
            )
        original = world.sink.insert
        failed = False

        async def interrupt(table, rows):
            nonlocal failed
            scope = rows[0].get("scope")
            selected = not failed and (
                failure == "second_kind"
                and scope == reconcile.card_scope("source")
                or failure == "ledger_ack"
                and scope == "cards"
                or failure == "fingerprint_ack"
                and table == reconcile.FINGERPRINT_TABLE
            )
            if selected:
                failed = True
                if failure != "second_kind":
                    await original(table, rows)  # Persisted before an uncertain acknowledgment.
                raise AnalyticsUnavailable("Interrupted durable owner enqueue")
            await original(table, rows)

        monkeypatch.setattr(world.sink, "insert", interrupt)
        with pytest.raises(AnalyticsUnavailable, match="Interrupted durable owner"):
            await world.worker._queue_step()
        assert failed
        fingerprints = world.sink.rows.get(reconcile.FINGERPRINT_TABLE, {})
        assert bool(fingerprints) == (failure == "fingerprint_ack")
        if fingerprints:
            ledger = world.sink.rows[reconcile.PROGRESS_TABLE][(world.worker.policy, "cards")]
            assert json.loads(ledger["cursor"])["todo"] == owners
        await world.worker.close()

        monkeypatch.setattr(world.sink, "insert", original)
        world.worker = reconcile.CurrentStateReconciler(
            world.sink, world.settings, world.connect, clock=lambda: world.now
        )
        await queue_cycle(world)
        assert world.worker._state("cards")["todo"] == owners
        assert len(world.sink.rows[reconcile.FINGERPRINT_TABLE]) == 2
        for kind in ("article", "source"):
            assert world.worker.progress[reconcile.card_scope(kind)]["pending"]
        assert not world.worker.progress["queues"]["pending"]
        while await world.worker._todo():
            pass
        # Dirty-card ACKs alone cannot certify the population and reverse tombstone scan.
        for kind in ("article", "source"):
            scope = reconcile.card_scope(kind)
            assert world.worker.progress[scope]["pending"]
            for _ in range(10):
                await world.worker._card_page(scope, current_only=True, selected_kind=kind)
                if not world.worker.progress[scope]["pending"]:
                    break
            else:
                pytest.fail("Replayed owner never completed independent source/target coverage")
        assert world.worker._state("cards") == {"todo": []}
        for kind, identity in (("article", article), ("source", source)):
            assert not world.sink.rows["analytics_card_canonical"][
                (world.worker.policy, kind, identity)
            ]["deleted"]
        await world.worker.close()

    asyncio.run(scenario())
    assert world.opened == world.closed


def test_upgrade_preserves_and_drains_existing_dirty_owners_without_adopting_old_coverage(
    monkeypatch,
):
    world = World(monkeypatch, batch=1)
    article = world.entity(value=101, terminal_at=NOW - timedelta(days=30))
    source = world.entity(kind="source", value=102)
    owners = ["article:" + article, "source:" + source]
    legacy = {
        "policy": world.worker.policy,
        "scope": "cards",
        "version": 1,
        "source_at": NOW,
        "completed_at": NOW,
        "pending": 1,
        "cursor": json.dumps(
            {
                "phase": "target",
                "index": 4,
                "started_at": NOW.isoformat(),
                "after": identifier(99),
                "todo": owners,
            }
        ),
    }
    world.sink.put(reconcile.PROGRESS_TABLE, [legacy])

    async def no_current_identities(*_args, **_kwargs):
        return []

    monkeypatch.setattr(sources, "card_ids", no_current_identities)

    async def scenario():
        await world.worker._load()
        status = await reconcile.read_reconciliation_status(world.sink, world.worker.policy)
        assert status["cards"]["source_at"] is None
        for _owner in owners:
            assert await world.worker._todo()
        assert not await world.worker._todo()
        # Old source/target sweep metadata is discarded, not resumed as a fake kind cycle.
        assert world.worker._state("cards") == {"todo": []}
        for kind, identity in (("article", article), ("source", source)):
            assert (
                world.sink.rows["analytics_card_canonical"][(world.worker.policy, kind, identity)][
                    "deleted"
                ]
                == 0
            )
        assert world.builds == 2
        status = await reconcile.read_reconciliation_status(world.sink, world.worker.policy)
        assert status["cards"]["source_at"] is None
        assert not any(table == "analytics_pipeline_events" for table, _ in world.sink.writes)

    asyncio.run(scenario())


def test_active_pass_remains_independent_of_unprocessed_dirty_owner_ledger(monkeypatch):
    world = World(monkeypatch)
    article = world.entity(has_active_work=True)

    async def scenario():
        await world.worker._enqueue(["article:" + article])
        for _ in reconcile.CARD_KINDS:
            await world.worker._card_page("active-cards", current_only=True, fast=True)
        assert not world.worker.progress["active-cards"]["pending"]
        assert world.worker._state("cards")["todo"] == ["article:" + article]
        assert world.worker.progress[reconcile.card_scope("article")]["pending"]
        assert world.worker.progress[reconcile.card_scope("article")]["completed_at"] is None

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.failure_recovery
def test_native_full_queue_set_survives_http_field_limit_and_uncertain_cleanup_ack(monkeypatch):
    """Large IN/NOT IN sets remain exact across retry and preserve newer observations."""
    from tests.support import analytics

    async def scenario():
        configured = analytics.settings()
        name = f"queue_transport_{uuid.uuid4().hex}_test"
        async with cards.AnalyticsClient(configured) as owner:
            await owner.execute(f"CREATE DATABASE `{name}`")
            try:
                settings = configured.model_copy(
                    update={"clickhouse_database": name, "analytics_reconcile_batch_size": 2}
                )
                async with cards.AnalyticsClient(settings) as client:
                    await client.initialize()
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await client.execute("SYSTEM STOP MERGES analytics_pipeline_events")
                    valid = {
                        str(uuid.uuid5(uuid.NAMESPACE_OID, str(value))) for value in range(20_000)
                    }
                    removed = min(valid)
                    stale = {identifier(value) for value in range(30_001, 30_004)}
                    newer = identifier(30_004)
                    rows = [
                        reconcile._base("queue", identity, 1)
                        | {"status": "queued", "task": "ingestion", "created_at": NOW}
                        for identity in valid | stale | {newer}
                    ]
                    await client.insert("analytics_pipeline_events", rows)
                    await client.insert(
                        "analytics_pipeline_events",
                        [
                            reconcile._base("queue", newer, 11)
                            | {"status": "running", "task": "ingestion", "created_at": NOW},
                            reconcile._base("queue", removed, 3, deleted=True),
                        ],
                    )
                    worker = reconcile.CurrentStateReconciler(client, settings, clock=lambda: NOW)
                    requests = []
                    original = client.query

                    async def capture(statement, parameters=None, **kwargs):
                        if (external := kwargs.get("external_data")) is not None:
                            assert not any(
                                isinstance(value, (list, set, tuple))
                                for value in (parameters or {}).values()
                            )
                            requests.append(len(external.files[0].data))
                        return await original(statement, parameters, **kwargs)

                    monkeypatch.setattr(client, "query", capture)
                    targets = await worker._queue_targets(sorted(valid))
                    assert set(targets) == valid
                    assert targets[removed]["revision"] == 3
                    assert targets[removed]["deleted"] == 1
                    assert requests == [320_000]

                    # Empty external sets also have native exclusion semantics.
                    empty = await client.query(
                        "SELECT count() AS n FROM analytics_pipeline_events FINAL "
                        "WHERE id NOT IN (SELECT id FROM reconcile_queue_ids)"
                        + reconcile.FINAL_GUARDS,
                        external_data=reconcile._queue_identifiers(set()),
                    )
                    assert empty == [{"n": 20_004}]
                    cycle = reconcile.QueueCycle(10, NOW, [], valid, {})
                    worker.queue_cycle = cycle
                    original_insert = client.insert
                    fail_ack = True

                    async def uncertain_ack(table, records):
                        nonlocal fail_ack
                        await original_insert(table, records)
                        if table == "analytics_pipeline_events" and fail_ack:
                            fail_ack = False
                            raise AnalyticsUnavailable("Uncertain cleanup acknowledgment")

                    monkeypatch.setattr(client, "insert", uncertain_ack)
                    with pytest.raises(AnalyticsUnavailable, match="Uncertain cleanup"):
                        await worker._absent_queue_targets(cycle)
                    assert cycle.cursor is None and worker.queue_cycle is cycle
                    assert "queues" not in worker.progress
                    # Retry observes already-persisted tombstones without deleting any
                    # member of the full valid set or a live row beyond this fence.
                    assert await worker._absent_queue_targets(cycle)
                    assert worker.queue_cycle is None
                    assert not worker.progress["queues"]["pending"]
                    actual = await worker._queue_targets(sorted(valid))
                    actual.update(await worker._queue_targets(sorted(stale | {newer})))
                    assert {identity for identity, row in actual.items() if row["deleted"]} == (
                        stale | {removed}
                    )
                    assert actual[newer]["revision"] == 11 and not actual[newer]["deleted"]
                    assert all(size <= 16 * reconcile.MAX_ACTIVE_JOBS for size in requests)
                    await worker.close()
                # External inputs are request-local and do not leave durable tables.
                async with cards.AnalyticsClient(settings) as restarted:
                    assert not await restarted.query("SHOW TABLES LIKE 'reconcile_queue_ids'")
                    assert (
                        len(
                            await reconcile.CurrentStateReconciler(
                                restarted, settings
                            )._queue_targets([newer])
                        )
                        == 1
                    )
            finally:
                await owner.execute(f"DROP DATABASE IF EXISTS `{name}` SYNC")

    asyncio.run(scenario())


@pytest.mark.integration
def test_native_card_lookup_prunes_shards_and_preserves_final_versions(monkeypatch):
    """The full canonical key prunes real marks without hiding newer tombstones."""
    import hashlib
    import re

    from tests.support import analytics

    async def scenario():
        configured = analytics.settings()
        name = f"card_lookup_{uuid.uuid4().hex}_test"
        async with cards.AnalyticsClient(configured) as owner:
            await owner.execute(f"CREATE DATABASE `{name}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": name})
                async with cards.AnalyticsClient(settings) as client:
                    await client.initialize()
                    await client.execute("SYSTEM STOP MERGES analytics_card_canonical")
                    worker = reconcile.CurrentStateReconciler(client, settings)
                    identities = [
                        uuid.UUID(
                            bytes=hashlib.sha256(str(value).encode()).digest()[:16], version=4
                        )
                        for value in range(100_000)
                    ]
                    await client.insert(
                        "analytics_card_canonical",
                        [
                            {
                                "policy": worker.policy,
                                "kind": "article",
                                "shard": identity.bytes[0],
                                "entity_id": str(identity),
                                "version": 1,
                                "deleted": 0,
                                "semantic_hash": str(value),
                            }
                            for value, identity in enumerate(identities)
                        ],
                    )
                    selected = [
                        *sorted(str(value) for value in identities if value.bytes[0] == 0)[:50],
                        *sorted(str(value) for value in identities if value.bytes[0] == 1)[:50],
                    ]
                    assert len(selected) == 100
                    await client.insert(
                        "analytics_card_canonical",
                        [
                            {
                                "policy": worker.policy,
                                "kind": "article",
                                "shard": cards.shard_of(identity),
                                "entity_id": identity,
                                "version": 2,
                                "deleted": deleted,
                                "semantic_hash": digest,
                            }
                            for identity, deleted, digest in (
                                (selected[0], 0, "updated"),
                                (selected[1], 1, "removed"),
                            )
                        ],
                    )
                    actual_query = []
                    original = client.query

                    async def capture(statement, parameters=None, **kwargs):
                        actual_query.append((statement, parameters))
                        return await original(statement, parameters, **kwargs)

                    monkeypatch.setattr(client, "query", capture)
                    actual = await worker._card_targets("article", selected)
                    statement, parameters = actual_query[-1]
                    legacy = statement.replace("AND shard IN {shards:Array(UInt16)} ", "")
                    expected = {
                        str(row["entity_id"]): row for row in await original(legacy, parameters)
                    }
                    assert actual == expected and len(actual) == 100
                    assert actual[selected[0]]["semantic_hash"] == "updated"
                    assert actual[selected[1]]["deleted"] == 1
                    assert actual[selected[1]]["semantic_hash"] == "removed"
                    plans = []
                    for query in (legacy, statement):
                        plan = await original("EXPLAIN indexes=1 " + query, parameters)
                        counts = re.findall(
                            r"Granules: (\d+)/(\d+)", "\n".join(row["explain"] for row in plan)
                        )
                        assert counts
                        plans.append(int(counts[-1][0]))
                    assert plans[1] < plans[0] / 2, plans
                    print(
                        json.dumps(
                            {
                                "fixture_rows": 100_000,
                                "selected_ids": 100,
                                "legacy_granules": plans[0],
                                "bounded_granules": plans[1],
                            }
                        )
                    )
            finally:
                await owner.execute(f"DROP DATABASE IF EXISTS `{name}` SYNC")

    asyncio.run(scenario())
