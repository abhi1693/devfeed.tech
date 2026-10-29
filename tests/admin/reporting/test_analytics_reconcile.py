"""Current-state repair, truthful coverage and bounded scheduling contracts."""

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import analytics_direct_cards as cards
from devfeed_core import analytics_reconcile as reconcile
from devfeed_core import analytics_reconcile_sources as sources
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.config import Settings

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

    async def query(self, statement, parameters=None):
        params = parameters or {}
        self.queries.append((statement, deepcopy(params)))
        if "FINAL" in statement:
            assert reconcile.FINAL_GUARDS in statement
        if self.fail_query is not None:
            raise self.fail_query
        self.world.events.append("ch:query")
        if reconcile.PROGRESS_TABLE in statement:
            rows = list(self.rows.get(reconcile.PROGRESS_TABLE, {}).values())
            return deepcopy([row for row in rows if row["policy"] == params["policy"]])
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
        await world.worker._card_page("cards", current_only=True)
        assert world.worker.progress["cards"]["completed_at"] is None
        assert world.worker.progress["cards"]["pending"] == 1
        for _ in range(30):
            world.now += timedelta(seconds=1)
            await world.worker._card_page("cards", current_only=True)
            if not world.worker.progress["cards"]["pending"]:
                break
        else:
            pytest.fail("Covered current-card pass never completed")

    asyncio.run(scenario())
    assert world.worker.progress["cards"]["source_at"] == NOW
    assert world.worker.progress["cards"]["completed_at"] > NOW
    assert world.builds == 1


def test_fence_precedes_snapshot_and_publish_lock_is_released_before_source_reads(monkeypatch):
    world = World(monkeypatch)
    world.job()
    asyncio.run(world.worker._snapshot())
    events = world.events
    lock = next(i for i, text in enumerate(events) if "pg_try_advisory_lock" in text)
    begin = events.index("begin")
    fence = next(i for i, text in enumerate(events) if "txid_current" in text)
    unlock = next(i for i, text in enumerate(events) if "pg_advisory_unlock" in text)
    source = next(i for i, text in enumerate(events) if "AS record" in text)
    assert lock < begin < fence < unlock < source < events.index("commit")
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
        return True

    async def todo():
        selected.append("todo")
        return True

    async def page(scope, **_options):
        selected.append(scope)
        return True

    monkeypatch.setattr(world.worker, "_lead", lead)
    monkeypatch.setattr(world.worker, "_load", load)
    monkeypatch.setattr(world.worker, "_queue_step", queue)
    monkeypatch.setattr(world.worker, "_todo", todo)
    monkeypatch.setattr(world.worker, "_card_page", page)
    monkeypatch.setattr(
        world.worker,
        "_state",
        lambda scope: {"todo": ["article:" + identifier(1)]} if scope == "cards" else {},
    )

    async def scenario():
        for _ in range(7):
            assert await world.worker.once()

    asyncio.run(scenario())
    assert set(selected) == {"queues", "todo", "active-cards", "cards", "sweep"}
    assert selected.count("queues") == selected.count("todo") == 2


def test_leader_loss_discards_uncovered_snapshot_and_releases_connection(monkeypatch):
    world = World(monkeypatch)
    world.sink.fail_query = AnalyticsUnavailable("Controlled connection loss")
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(world.worker.once())
    assert world.worker._leader is None and world.worker.queue_cycle is None
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
        "scope": "cards",
        "pending": 0,
        "version": 1,
        "completed_at": NOW.isoformat(),
        "source_at": NOW.isoformat(),
    }
    world.sink.put(reconcile.PROGRESS_TABLE, [row])
    status = asyncio.run(reconcile.read_reconciliation_status(world.sink, world.worker.policy))
    assert status["cards"]["source_at"] == NOW and not status["cards"]["pending"]

    async def duplicates(*_args):
        return [row, row]

    monkeypatch.setattr(world.sink, "query", duplicates)
    with pytest.raises(ValueError, match="Ambiguous"):
        asyncio.run(reconcile.read_reconciliation_status(world.sink, world.worker.policy))


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
        assert not world.worker.progress["cards"]["pending"]
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
    with pytest.raises(AnalyticsUnavailable, match="busy"):
        asyncio.run(world.worker._refresh("article", [identifier(1)]))
    assert not world.sink.writes


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
        asyncio.run(world.worker._card_page("cards", current_only=True))
    world.sink.after_ack = None
    world.worker = reconcile.CurrentStateReconciler(
        world.sink, world.settings, world.connect, clock=lambda: world.now
    )

    async def scenario():
        await world.worker._load()
        for _ in range(30):
            await world.worker._card_page("cards", current_only=True)
            if not world.worker.progress["cards"]["pending"]:
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
    world.worker.progress["cards"] = {"cursor": json.dumps(state)}
    with pytest.raises(AnalyticsUnavailable):
        asyncio.run(world.worker._card_page("cards", current_only=True))
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
