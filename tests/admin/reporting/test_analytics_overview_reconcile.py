"""Retained facts recover without resurrecting queues or claiming partial coverage."""

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import analytics_overview_reconcile as repair
from devfeed_core.analytics import AnalyticsPreparing, AnalyticsUnavailable
from devfeed_core.config import get_settings

NOW = datetime(2026, 10, 30, 4, tzinfo=UTC)


def uid(value):
    return str(uuid.UUID(int=value))


class Result:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return deepcopy(self.rows)


class Connection:
    def __init__(self, world):
        self.world = world

    async def execute(self, statement, parameters=()):
        text = statement if isinstance(statement, str) else statement.as_string()
        self.world.reads.append((text, deepcopy(parameters)))
        if "FROM articles a WHERE a.id=ANY" in text:
            return Result(
                [
                    dict(row, source_ids=[], topic_ids=[], automated=False)
                    for key, row in self.world.records["articles"].items()
                    if key in parameters[0]
                ]
            )
        if "FROM user_accounts a LEFT JOIN" in text:
            return Result(
                [
                    {
                        "id": key,
                        "created_at": row["created_at"],
                        "computed_at": NOW,
                        "interest_count": 1,
                        "generation": 1,
                        "invalidated": False,
                        "expires_at": NOW + timedelta(hours=1),
                        "next_refresh_at": NOW,
                        "has_recommendations": True,
                    }
                    for key, row in self.world.records["user_accounts"].items()
                    if key in parameters[0]
                ]
            )
        name = re.search(r'FROM "([a-z_]+)"', text)[1]
        keys = repair.TABLES[name].keys
        values = list(self.world.records[name].values())

        def order(row):
            return tuple(str(row[key]) for key in keys)

        values.sort(key=order)
        if "DESC" in text:
            return Result([{key: values[-1][key] for key in keys}] if values else [])
        if " AS record" in text:
            if self.world.before_fetch:
                self.world.before_fetch()
                values = list(self.world.records[name].values())
            selected = {
                tuple(str(value) for value in parameters[index : index + len(keys)])
                for index in range(0, len(parameters), len(keys))
            }
            return Result(
                [
                    {
                        "record": {key: row.get(key) for key in repair.TABLES[name].fields},
                        "stamp": row.get("stamp", "1"),
                    }
                    for row in values
                    if order(row) in selected
                ]
            )
        upper = tuple(str(value) for value in parameters[: len(keys)])
        after = (
            tuple(str(value) for value in parameters[len(keys) : -1])
            if len(parameters) > len(keys) + 1
            else None
        )
        values = [
            row for row in values if order(row) <= upper and (after is None or order(row) > after)
        ][: parameters[-1]]
        return Result(
            [{**{key: row[key] for key in keys}, "stamp": row.get("stamp", "1")} for row in values]
        )


class Client:
    def __init__(self, world):
        self.world, self.settings = world, world.settings
        self.rows, self.writes, self.queries = {}, [], []
        self.fail_before = self.fail_after = self.on_target = None

    @staticmethod
    def key(table, row):
        if table == repair.PROGRESS_TABLE:
            return row["policy"], row["scope"]
        if table == repair.FINGERPRINT_TABLE:
            return row["policy"], row["kind"], str(row["entity_id"])
        return row["domain"], str(row["id"])

    def put(self, table, rows):
        target = self.rows.setdefault(table, {})
        for row in deepcopy(rows):
            key = self.key(table, row)
            version = row.get("revision", row.get("version", 0))
            old = target.get(key, {})
            if version >= old.get("revision", old.get("version", 0)):
                target[key] = row

    async def insert(self, table, rows):
        assert not self.world.source_open
        if self.fail_before == table:
            raise AnalyticsUnavailable("Controlled destination failure")
        self.writes.append((table, deepcopy(rows)))
        self.put(table, rows)
        if self.fail_after == table:
            raise AnalyticsUnavailable("Acknowledgement was interrupted")

    async def query(self, statement, parameters=None):
        assert not self.world.source_open
        assert repair.FINAL_GUARDS in statement
        params = parameters or {}
        self.queries.append((statement, deepcopy(params)))
        table = re.search(r"FROM ([a-z_]+) FINAL", statement)[1]
        rows = list(self.rows.get(table, {}).values())
        if table == repair.PROGRESS_TABLE:
            return deepcopy(
                [
                    row
                    for row in rows
                    if row["policy"] == params["policy"] and row["scope"] in params["scopes"]
                ]
            )
        if table == repair.FINGERPRINT_TABLE:
            return deepcopy(
                [
                    row
                    for row in rows
                    if row["policy"] == params["policy"]
                    and row["kind"] == params["kind"]
                    and str(row["entity_id"]) in params["ids"]
                    and not row["deleted"]
                ]
            )
        rows = [row for row in rows if row["domain"] in params["domains"]]
        if "ids" in params:
            return deepcopy([row for row in rows if str(row["id"]) in params["ids"]])
        rows = [
            row
            for row in rows
            if not row["deleted"]
            and row["revision"] <= params["fence"]
            and (not params["task"] or row["task"] == params["task"])
        ]
        if "after_id" in params:
            rows = [
                row
                for row in rows
                if (row["domain"], str(row["id"])) > (params["after_domain"], params["after_id"])
            ]
        rows.sort(key=lambda row: (row["domain"], str(row["id"])))
        result = [{"domain": row["domain"], "id": row["id"]} for row in rows[: params["limit"]]]
        if self.on_target:
            self.on_target()
        return deepcopy(result)


class World:
    def __init__(self, *, batch=2):
        self.now, self.revision, self.source_open = NOW, 100, False
        self.settings = get_settings().model_copy(
            update={
                "analytics_backfill_batch_size": batch,
                "analytics_reconcile_sweep_seconds": 30,
            }
        )
        self.records = {source: {} for source in repair.OVERVIEW_SOURCES}
        self.reads = []
        self.before_fetch = None
        self.client = Client(self)
        self.worker = repair.OverviewReconciler(
            self.client, self.settings, self.snapshot, policy="test-policy", clock=lambda: self.now
        )

    @asynccontextmanager
    async def snapshot(self):
        assert not self.source_open
        self.revision += 1
        self.source_open = True
        try:
            yield Connection(self), self.revision, self.now
        finally:
            self.source_open = False

    def record(self, source, value=1, **changes):
        row = {
            "id": uid(value),
            "user_id": uid(value),
            "topic_id": uid(value),
            "created_at": NOW - timedelta(days=3),
            "discovered_at": NOW - timedelta(days=3),
            "published_to_feed_at": NOW - timedelta(hours=1),
            "publication_status": "published",
            "name": "Retained source",
            "approval_status": "approved",
            "enabled": True,
            "consecutive_failures": 0,
            "status": "succeeded",
            "reason": "related_topic",
            "reviewed_at": NOW - timedelta(hours=2),
            "finished_at": NOW - timedelta(minutes=10),
            "available_at": NOW,
            "attempts": 2,
            "outcome": "enriched",
            "duration_ms": 12,
            "stamp": "1",
            **changes,
        }
        if source == "topic_proposals" and "status" not in changes:
            row["status"] = "approved"
        key = (
            uid(value)
            if len(repair.TABLES[source].keys) == 1
            else tuple(row[name] for name in repair.TABLES[source].keys)
        )
        self.records[source][key] = row
        if source in repair.FEED_SOURCES and source != "user_accounts":
            self.record("user_accounts", value)
        return row

    def progress(self, source):
        return self.client.rows.get(repair.PROGRESS_TABLE, {}).get(
            ("test-policy", repair.scope_for(source))
        )

    async def finish(self, source, *, restart=False):
        for _ in range(50):
            await self.worker.once(source)
            if restart:
                self.worker = repair.OverviewReconciler(
                    self.client,
                    self.settings,
                    self.snapshot,
                    policy="test-policy",
                    clock=lambda: self.now,
                )
            if not self.progress(source)["pending"]:
                return
        pytest.fail("The bounded retained-source cycle did not finish")


@pytest.mark.parametrize("source", repair.OVERVIEW_SOURCES)
def test_retained_sources_recover_stable_facts_and_never_emit_queue_rows(source):
    async def run():
        world = World(batch=1)
        world.record(source, 1)
        world.record(source, 2)
        await world.finish(source, restart=True)
        events = [
            row
            for table in (repair.PIPELINE_TABLE, repair.USER_TABLE)
            for row in world.client.rows.get(table, {}).values()
        ]
        assert events and all(row["domain"] != "queue" for row in events)
        if source in repair.JOB_TABLES:
            assert all(row["domain"] == "job" and row["attempt"] == 2 for row in events)
        if source == "articles":
            assert all(row["occurred_at"] == NOW - timedelta(hours=1) for row in events)
            assert all(row["created_at"] == NOW - timedelta(days=3) for row in events)
        assert world.progress(source)["source_at"] == NOW
        assert not await world.worker.once(source)
        assert all(
            "inference_calls" not in sql and "overview_daily" not in sql for sql, _ in world.reads
        )

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["before", "after"])
def test_destination_ack_precedes_fingerprints_and_cursor_even_on_ambiguous_retry(failure):
    async def run():
        world = World()
        world.record("articles")
        await world.worker.once("articles")
        before = deepcopy(world.progress("articles"))
        setattr(world.client, "fail_" + failure, repair.PIPELINE_TABLE)
        with pytest.raises(AnalyticsUnavailable):
            await world.worker.once("articles")
        pending = json.loads(world.progress("articles")["cursor"])
        assert pending["after"] == json.loads(before["cursor"])["after"]
        assert pending["pending_page"]["keys"] == [[uid(1)]]
        assert not world.client.rows.get(repair.FINGERPRINT_TABLE)
        setattr(world.client, "fail_" + failure, None)
        await world.finish("articles", restart=True)
        live = [r for r in world.client.rows[repair.PIPELINE_TABLE].values() if not r["deleted"]]
        assert len(live) == 1
        first_data = next(
            i for i, (table, _) in enumerate(world.client.writes) if table == repair.PIPELINE_TABLE
        )
        first_fingerprint = next(
            i
            for i, (table, _) in enumerate(world.client.writes)
            if table == repair.FINGERPRINT_TABLE
        )
        assert first_data < first_fingerprint

    asyncio.run(run())


def test_deleted_source_is_removed_but_later_live_writer_survives_start_fence():
    async def run():
        world = World()
        world.record("articles")
        await world.finish("articles")
        world.records["articles"].clear()
        world.now += timedelta(seconds=31)
        await world.worker.once("articles")
        await world.worker.once("articles")
        fence = json.loads(world.progress("articles")["cursor"])["fence"]

        def later():
            world.client.on_target = None
            world.record("articles", published_to_feed_at=NOW)
            world.revision = fence + 10
            row = repair._base("publication", uid(1), fence + 10)
            row["occurred_at"] = NOW
            world.client.put(repair.PIPELINE_TABLE, [row])

        world.client.on_target = later
        await world.finish("articles")
        assert not world.client.rows[repair.PIPELINE_TABLE]["publication", uid(1)]["deleted"]
        world.records["articles"].clear()
        world.now += timedelta(seconds=31)
        await world.finish("articles")
        assert world.client.rows[repair.PIPELINE_TABLE]["publication", uid(1)]["deleted"]

    asyncio.run(run())


def test_unchanged_source_metadata_skips_hydration_and_missing_destination_is_repaired():
    async def run():
        world = World()
        world.record("sources")
        await world.finish("sources")
        world.reads.clear()
        world.client.writes.clear()
        world.now += timedelta(seconds=31)
        await world.finish("sources")
        assert not any(" AS record" in statement for statement, _ in world.reads)
        assert not any(table == repair.PIPELINE_TABLE for table, _ in world.client.writes)
        world.client.rows[repair.PIPELINE_TABLE].clear()
        world.now += timedelta(seconds=31)
        await world.finish("sources")
        assert ("source", uid(1)) in world.client.rows[repair.PIPELINE_TABLE]

    asyncio.run(run())


def test_fixed_source_ceiling_defers_new_keys_and_scopes_progress_independently():
    async def run():
        world = World(batch=1)
        world.record("articles", 1)
        await world.worker.once("articles")
        world.record("articles", 2)
        await world.finish("articles")
        assert ("publication", uid(2)) not in world.client.rows[repair.PIPELINE_TABLE]
        snapshot = await repair.read_overview_snapshot(
            world.client, "test-policy", ["articles"], NOW, generation="1"
        )
        assert snapshot.status == "fresh"
        with pytest.raises(AnalyticsPreparing):
            await repair.read_overview_snapshot(
                world.client, "test-policy", ["articles", "ingestion_jobs"], NOW, generation="1"
            )
        world.now += timedelta(seconds=31)
        await world.finish("articles")
        assert ("publication", uid(2)) in world.client.rows[repair.PIPELINE_TABLE]

    asyncio.run(run())


@pytest.mark.parametrize(
    "state",
    [
        [],
        {"phase": "unknown"},
        {"phase": "source", "fence": 0},
        {"phase": "source", "fence": 1, "started_at": None},
        {"phase": "source", "fence": 1, "started_at": NOW.isoformat(), "upper": [uid(1), uid(2)]},
        {
            "phase": "target",
            "fence": 1,
            "started_at": NOW.isoformat(),
            "upper": [],
            "target_after": [1],
        },
    ],
)
def test_corrupt_checkpoints_cannot_certify_coverage(state):
    with pytest.raises((AnalyticsUnavailable, ValueError)):
        repair._cursor("articles", {"cursor": json.dumps(state)})


@pytest.mark.parametrize("descriptor", ["[]", '["bad","publication","bad","bad"]'])
def test_invalid_presence_references_are_rejected(descriptor):
    with pytest.raises((AnalyticsUnavailable, ValueError)):
        repair._references([descriptor])


def test_scheduler_admits_each_source_and_eventually_becomes_idle():
    async def run():
        world = World()
        for _ in range(100):
            if not await world.worker.once():
                break
        else:
            pytest.fail("Empty finite source cycles never became idle")
        assert not world.worker.due
        assert all(world.progress(source)["pending"] == 0 for source in repair.OVERVIEW_SOURCES)
        with pytest.raises(ValueError, match="Unknown retained"):
            await world.worker.once("inference_calls")

    asyncio.run(run())


def test_source_deletion_between_discovery_and_fetch_cannot_ack_a_phantom():
    async def run():
        world = World()
        world.record("articles")
        await world.finish("articles")
        world.records["articles"][uid(1)]["stamp"] = "2"
        world.now += timedelta(seconds=31)
        world.before_fetch = lambda: world.records["articles"].clear()
        await world.finish("articles")
        assert world.client.rows[repair.PIPELINE_TABLE]["publication", uid(1)]["deleted"]

    asyncio.run(run())


def test_recovery_rejects_queue_observations_before_any_destination_write():
    async def run():
        world = World()
        record = world.record("article_analysis_jobs")
        unsafe = {
            "record": record,
            "stamp": "1",
            "changes": [
                {
                    "source_table": "article_analysis_jobs",
                    "record": record,
                }
            ],
        }
        with pytest.raises(AnalyticsUnavailable, match="cannot publish queue"):
            await world.worker._prepare("article_analysis_jobs", [unsafe], {}, 100)
        assert not world.client.writes

    asyncio.run(run())


@pytest.mark.parametrize("sources", [[], ["articles", "articles"]])
def test_coverage_requires_an_explicit_distinct_source_set(sources):
    async def run():
        world = World()
        with pytest.raises(ValueError, match="distinct retained"):
            await repair.read_overview_snapshot(
                world.client, "test-policy", sources, NOW, generation="1"
            )

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["coverage", "fingerprint", "target", "progress", "pending"])
def test_ambiguous_or_invalid_destination_metadata_fails_closed(fault):
    async def run():
        world = World()
        good = {
            "scope": repair.scope_for("articles"),
            "source_at": NOW,
            "completed_at": NOW,
            "pending": 2 if fault == "pending" else 0,
        }
        target = repair._base("publication", uid(1), 10)

        async def bad_query(_statement, _parameters=None):
            if fault == "fingerprint":
                return [{"entity_id": uid(1)}] * 2
            if fault == "target":
                return [target, target]
            return [good] if fault == "pending" else [good, good]

        world.client.query = bad_query
        with pytest.raises(AnalyticsUnavailable):
            if fault == "fingerprint":
                await world.worker._fingerprints("overview-source:articles", [uid(1)])
            elif fault == "target":
                await world.worker._targets([repair._descriptor(target)])
            elif fault == "progress":
                await world.worker.once("articles")
            else:
                await repair.read_overview_snapshot(
                    world.client, "test-policy", ["articles"], NOW, generation="1"
                )

    asyncio.run(run())


def test_timestamp_validation_normalizes_naive_values_and_rejects_non_dates():
    assert repair._time(NOW.replace(tzinfo=None)) == NOW
    assert repair._time(NOW.isoformat()) == NOW
    with pytest.raises(AnalyticsUnavailable, match="timestamp"):
        repair._time(123)


@pytest.mark.parametrize("source", ["articles", "article_analysis_jobs", "topic_proposals"])
def test_unknown_insert_then_deleted_source_restarts_without_ghosts_or_lost_immutable_facts(source):
    async def run():
        world = World()
        world.record(source)
        original = world.client.insert
        delayed = []

        async def buffered(table, rows):
            if table == repair.PIPELINE_TABLE:
                delayed.extend(deepcopy(rows))
                raise AnalyticsUnavailable("Connection cancelled with unknown insert outcome")
            await original(table, rows)

        await world.worker.once(source)
        world.client.insert = buffered
        with pytest.raises(AnalyticsUnavailable):
            await world.worker.once(source)
        checkpoint = json.loads(world.progress(source)["cursor"])
        assert checkpoint["pending_page"]["keys"] == [[uid(1)]]
        assert world.progress(source)["completed_at"] is None
        world.records[source].clear()
        world.client.insert = original
        await world.finish(source, restart=True)
        world.client.put(repair.PIPELINE_TABLE, delayed)
        events = list(world.client.rows[repair.PIPELINE_TABLE].values())
        if source == "articles":
            assert all(row["deleted"] for row in events)
        else:
            assert len(events) == 1 and events[0]["domain"] == "job" and not events[0]["deleted"]
        assert "pending_page" not in json.loads(world.progress(source)["cursor"])

    asyncio.run(run())


def test_pending_original_attempt_survives_a_retry_and_next_retained_attempt():
    async def run():
        world = World()
        world.record("article_analysis_jobs", attempts=1)
        await world.worker.once("article_analysis_jobs")
        world.client.fail_before = repair.PIPELINE_TABLE
        with pytest.raises(AnalyticsUnavailable):
            await world.worker.once("article_analysis_jobs")
        world.record("article_analysis_jobs", attempts=2, stamp="2")
        world.client.fail_before = None
        await world.finish("article_analysis_jobs", restart=True)
        events = list(world.client.rows[repair.PIPELINE_TABLE].values())
        assert sorted(row["attempt"] for row in events) == [1, 2]
        assert all(row["domain"] == "job" for row in events)

    asyncio.run(run())


def test_completed_topic_decision_survives_source_deletion_and_reopened_proposal():
    async def run():
        world = World()
        world.record("topic_proposals")
        await world.finish("topic_proposals")
        original = deepcopy(world.client.rows[repair.PIPELINE_TABLE])
        world.records["topic_proposals"].clear()
        world.now += timedelta(seconds=31)
        await world.finish("topic_proposals")
        assert world.client.rows[repair.PIPELINE_TABLE] == original
        world.record("topic_proposals", status="pending", reviewed_at=None)
        world.now += timedelta(seconds=31)
        await world.finish("topic_proposals")
        assert world.client.rows[repair.PIPELINE_TABLE] == original

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"revision": -1},
        {"revision": -1, "keys": [[uid(1)]], "targets": [], "immutable": []},
        {
            "revision": 1,
            "keys": [],
            "targets": [],
            "immutable": [],
        },
        {"revision": 1, "keys": [[uid(1)]], "targets": None, "immutable": []},
    ],
)
def test_pending_page_shapes_fail_closed(bad):
    with pytest.raises(AnalyticsUnavailable):
        repair._pending("articles", bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task", "unknown"),
        ("status", "running"),
        ("occurred_at", None),
        ("revision", -1),
        ("attempt", True),
        ("duration_ms", -1),
        ("outcome", ""),
    ],
)
def test_retained_pending_payloads_cannot_invent_untyped_attempts(field, value):
    record = {
        "id": uid(1),
        "revision": 10,
        "task": "analysis",
        "status": "succeeded",
        "occurred_at": NOW,
        "outcome": "applied",
        "attempt": 1,
        "duration_ms": None,
    }
    record[field] = value
    with pytest.raises(AnalyticsUnavailable):
        repair._immutable(record)


def test_pending_payload_attribution_and_cursor_size_are_bounded():
    record = {
        "id": uid(1),
        "revision": 11,
        "task": "analysis",
        "status": "succeeded",
        "occurred_at": NOW,
        "outcome": "applied",
        "attempt": 1,
        "duration_ms": None,
    }
    with pytest.raises(AnalyticsUnavailable, match="does not match"):
        repair._pending(
            "article_analysis_jobs",
            {"revision": 10, "keys": [[uid(1)]], "targets": [], "immutable": [record]},
        )
    with pytest.raises(AnalyticsUnavailable, match="exceeds"):
        repair._cursor("articles", {"cursor": "x" * 2_000_001})
    with pytest.raises(AnalyticsUnavailable, match="immutable observation"):
        repair._immutable({"prompt": "must not enter a checkpoint"})


def test_pending_page_cannot_ack_an_unrecoverable_owner_or_queue_row(monkeypatch):
    async def run():
        world = World()
        world.record("articles")
        await world.worker.once("articles")
        world.client.fail_before = repair.PIPELINE_TABLE
        with pytest.raises(AnalyticsUnavailable):
            await world.worker.once("articles")
        world.client.fail_before = None
        progress = world.progress("articles")
        state = json.loads(progress["cursor"])
        state["pending_page"]["targets"].append(
            repair._descriptor(repair._base("publication", uid(2), 1))
        )
        progress["cursor"] = json.dumps(state)
        with pytest.raises(AnalyticsUnavailable, match="owner could not"):
            await world.worker.once("articles")
        state["pending_page"]["targets"].pop()
        progress["cursor"] = json.dumps(state)

        async def unsafe(projection, changes, *, revision):
            projection.rows.append(repair._base("queue", uid(1), revision))

        monkeypatch.setattr(repair._Projection, "publish", unsafe)
        with pytest.raises(AnalyticsUnavailable, match="cannot replay queue"):
            await world.worker.once("articles")

    asyncio.run(run())


def test_checkpoint_size_is_checked_before_any_destination_write():
    async def run():
        world = World()
        with pytest.raises(AnalyticsUnavailable, match="page exceeds"):
            await world.worker._save(
                "articles", {"started_at": NOW.isoformat(), "large": "x" * 2_000_001}, None
            )
        assert not world.client.writes

    asyncio.run(run())
