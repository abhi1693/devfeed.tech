"""Finite audience metadata repair uses actual dates and acknowledged bounded pages."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import analytics_audience_refresh as refresh
from devfeed_core import analytics_producer
from devfeed_core.analytics import AnalyticsUnavailable

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def identity(value):
    return str(uuid.UUID(int=value))


class Result:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return deepcopy(self.rows)


class World:
    def __init__(self, monkeypatch):
        self.sources = {table: [] for table, _, _ in refresh.SOURCES}
        self.progress = None
        self.data = {}
        self.queries, self.writes, self.source_reads = [], [], []
        self.open = False
        self.busy = False
        self.revision = 100
        self.fail = None
        self.after_write = None
        self.after_holes = None

        async def feeds(_connection, identifiers):
            assert self.open
            assert len(identifiers) <= 100
            return {
                row["id"]: {
                    "user_id": row["id"],
                    "created_at": row["created_at"],
                    "computed_at": row.get("computed_at"),
                }
                for row in self.sources["user_accounts"]
                if row["id"] in identifiers
            }

        monkeypatch.setattr(refresh, "_feed_rows", feeds)

    @asynccontextmanager
    async def snapshot(self):
        if self.busy:
            yield None
            return
        assert not self.open
        self.open = True
        self.revision += 1
        try:
            yield self, self.revision, NOW
        finally:
            self.open = False

    async def execute(self, statement, parameters):
        assert self.open
        statement = statement.as_string(None)
        self.source_reads.append((statement, parameters))
        table, keys, _ = next(item for item in refresh.SOURCES if f'"{item[0]}"' in statement)
        rows = [
            row
            for row in self.sources[table]
            if row.get("reason", "related_topic") == "related_topic"
        ]

        def key(row):
            return tuple(uuid.UUID(row[name]) for name in keys)

        if "DESC" in statement:
            return Result(sorted(rows, key=key, reverse=True)[: parameters[-1]])
        upper = tuple(uuid.UUID(value) for value in parameters[: len(keys)])
        after = tuple(uuid.UUID(value) for value in parameters[len(keys) : -1])
        rows = [row for row in rows if key(row) <= upper and (not after or key(row) > after)]
        return Result(sorted(rows, key=key)[: parameters[-1]])

    async def query(self, statement, parameters=None):
        assert not self.open
        self.queries.append((statement, parameters))
        if "analytics:audience-metadata-holes" in statement:
            rows = [
                row
                for row in self.data.values()
                if row["revision"] <= (parameters or {}).get("fence", 2**64 - 1)
                if not row.get("deleted")
                and (
                    row["domain"] == "feed"
                    and row.get("created_at") is None
                    or row["domain"] == "membership"
                    and (
                        row.get("user_id") is None
                        or row["task"] == "follow"
                        and row.get("occurred_at") is None
                    )
                )
            ][: parameters["limit"]]
            result = deepcopy(rows)
            if self.after_holes:
                self.after_holes()
            return result
        assert parameters == {"policy": refresh.REFRESH_POLICY, "scope": refresh.REFRESH_SCOPE}
        assert refresh.FINAL_GUARDS in statement
        return [deepcopy(self.progress)] if self.progress else []

    async def insert(self, table, rows):
        assert not self.open
        self.writes.append((table, deepcopy(rows)))
        failure, self.fail = self.fail, None
        if failure == "before":
            raise AnalyticsUnavailable("Unacknowledged data")
        if table == refresh.PROGRESS_TABLE:
            self.progress = deepcopy(rows[0])
        else:
            assert table == "analytics_user_events"
            for row in deepcopy(rows):
                key = row["domain"], row["id"]
                if row["revision"] >= self.data.get(key, {}).get("revision", 0):
                    self.data[key] = row
        if self.after_write:
            self.after_write(table, rows)
        if failure == "after":
            raise AnalyticsUnavailable("Acknowledgement interrupted")

    def worker(self):
        return refresh.AudienceMetadataRefresh(self, self.snapshot)


async def finish(worker):
    for _ in range(30):
        if not await worker.once():
            return
    pytest.fail("Finite metadata repair did not finish")


def test_refresh_preserves_actual_times_nulls_and_real_membership_owner(monkeypatch):
    world = World(monkeypatch)
    created, computed = NOW - timedelta(days=100), NOW - timedelta(hours=1)
    world.sources["user_accounts"] = [
        {"id": identity(0), "created_at": created},
        {"id": identity(1), "created_at": created, "computed_at": computed},
    ]
    world.sources["user_topics"] = [
        {"user_id": identity(0), "topic_id": identity(2), "occurred_at": created}
    ]
    world.sources["user_interests"] = [
        {"user_id": identity(1), "topic_id": identity(3), "occurred_at": computed},
        {"user_id": identity(0), "topic_id": identity(4), "occurred_at": None},
        {
            "user_id": identity(1),
            "topic_id": identity(5),
            "reason": "follow",
            "occurred_at": computed,
        },
    ]

    async def run():
        assert not await refresh.audience_metadata_ready(world)
        worker = world.worker()
        await finish(worker)
        assert await refresh.audience_metadata_ready(world)
        reads = len(world.source_reads)
        assert not await world.worker().once()
        assert len(world.source_reads) == reads

    asyncio.run(run())
    feeds = {row["id"]: row for row in world.data.values() if row["domain"] == "feed"}
    assert feeds[identity(0)]["occurred_at"] is None
    assert feeds[identity(0)]["created_at"] == created
    assert feeds[identity(1)]["occurred_at"] == computed
    memberships = {
        row["target_id"]: row for row in world.data.values() if row["domain"] == "membership"
    }
    assert memberships[identity(2)]["occurred_at"] == created
    assert memberships[identity(3)]["occurred_at"] == computed
    assert memberships[identity(3)]["user_id"] == identity(1)
    assert memberships[identity(4)]["occurred_at"] is None
    assert identity(5) not in memberships
    assert world.progress["pending"] == 0
    assert json.loads(world.progress["cursor"])["phase"] == refresh.COMPLETE_PHASE


def test_fixed_ceiling_and_keyset_pages_never_expand_to_new_accounts(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(i), "created_at": NOW} for i in range(101)]

    async def run():
        worker = world.worker()
        assert await worker.once()
        assert json.loads(world.progress["cursor"])["upper"] == [identity(100)]
        world.sources["user_accounts"].append({"id": identity(101), "created_at": NOW})
        assert await worker.once()
        assert len(world.data) == 100
        state = json.loads(world.progress["cursor"])
        assert state["after"] == [identity(99)]
        assert not await refresh.audience_metadata_ready(world)
        await finish(world.worker())

    asyncio.run(run())
    assert len(world.data) == 101
    assert ("feed", identity(101)) not in world.data
    pages = [rows for table, rows in world.writes if table == "analytics_user_events"]
    assert [len(rows) for rows in pages] == [100, 1]
    assert any('("source"."id")>(%s::uuid)' in statement for statement, _ in world.source_reads)


@pytest.mark.parametrize("failure", ["before", "after"])
def test_data_failure_does_not_advance_cursor_and_restart_is_idempotent(monkeypatch, failure):
    world = World(monkeypatch)
    world.sources["user_topics"] = [
        {"user_id": identity(1), "topic_id": identity(i), "occurred_at": NOW} for i in range(201)
    ]

    async def run():
        worker = world.worker()
        for _ in range(3):
            assert await worker.once()
        before = deepcopy(world.progress)
        world.fail = failure
        with pytest.raises(AnalyticsUnavailable):
            await worker.once()
        assert world.progress == before
        assert not world.open
        await finish(world.worker())
        assert await refresh.audience_metadata_ready(world)

    asyncio.run(run())
    assert len(world.data) == 201
    assert max(len(rows) for table, rows in world.writes if table == "analytics_user_events") == 200


def test_late_live_revision_survives_refresh_and_busy_lock_has_no_checkpoint(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(1), "created_at": NOW}]

    async def run():
        worker = world.worker()
        world.busy = True
        assert not await worker.once()
        assert world.progress is None
        world.busy = False
        await worker.once()
        world.data[("feed", identity(1))] = {
            "domain": "feed",
            "id": identity(1),
            "revision": 10_000,
            "occurred_at": NOW,
            "created_at": NOW,
        }
        await finish(worker)

    asyncio.run(run())
    assert world.data[("feed", identity(1))]["revision"] == 10_000
    assert world.data[("feed", identity(1))]["occurred_at"] == NOW


def test_old_producer_after_completion_cannot_certify_missing_metadata(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(1), "created_at": NOW}]

    async def run():
        worker = world.worker()
        await finish(worker)
        assert worker.complete and not worker.due
        assert await refresh.audience_metadata_ready(world)
        reads = len(world.source_reads)
        assert not await worker.once()
        assert len(world.source_reads) == reads
        world.revision += 10
        world.progress["version"] += 10**15
        previous_version = world.progress["version"]
        world.data[("feed", identity(1))] = {
            "domain": "feed",
            "id": identity(1),
            "revision": world.revision,
            "created_at": None,
            "occurred_at": None,
        }
        assert not await refresh.audience_metadata_ready(world)
        assert await worker.once()
        assert not worker.complete and worker.due
        assert world.progress["pending"] == 1
        assert world.progress["version"] > previous_version
        await finish(worker)
        assert await refresh.audience_metadata_ready(world)
        assert world.data[("feed", identity(1))]["created_at"] == NOW

    asyncio.run(run())


@pytest.mark.parametrize(
    "state",
    [
        [],
        {"phase": -1},
        {"phase": 5},
        {"phase": True},
        {"phase": 0, "upper": ["invalid"]},
        {"phase": 0, "start_revision": -1},
        {"phase": 0, "start_revision": True},
        {"phase": 0, "start_revision": 2**64},
        {"phase": 0, "upper": ["invalid"], "start_revision": 1},
        {"phase": 1, "upper": [identity(1)], "start_revision": 1},
        {"phase": 3, "upper": [identity(1)], "start_revision": 1},
        {"phase": 4, "upper": None, "after": None, "start_revision": 1},
    ],
)
def test_invalid_durable_cursor_never_advances_or_claims_ready(monkeypatch, state):
    world = World(monkeypatch)
    world.progress = {"cursor": json.dumps(state), "pending": 1}
    with pytest.raises((ValueError, AnalyticsUnavailable)):
        asyncio.run(world.worker().once())
    assert world.writes == [] and world.source_reads == []


def test_ambiguous_progress_is_rejected_by_reader_and_worker():
    class Ambiguous:
        async def query(self, *_args):
            return [{}, {}]

    with pytest.raises(AnalyticsUnavailable, match="Ambiguous"):
        asyncio.run(refresh.audience_metadata_ready(Ambiguous()))
    with pytest.raises(AnalyticsUnavailable, match="Ambiguous"):
        asyncio.run(refresh.AudienceMetadataRefresh(Ambiguous(), None).once())


def test_publisher_busy_between_key_boundary_and_page_leaves_cursor_unchanged(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(1), "created_at": NOW}]

    async def run():
        worker = world.worker()
        await worker.once()
        previous = deepcopy(world.progress)
        world.busy = True
        assert not await worker.once()
        assert world.progress == previous and not world.data

    asyncio.run(run())


def test_page_overflow_and_unexpected_target_domain_fail_closed(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(1), "created_at": NOW}]

    async def run():
        worker = world.worker()
        await worker.once()
        previous = deepcopy(world.progress)

        async def too_many(*_args):
            return Result([{"id": identity(1)}] * 101)

        monkeypatch.setattr(world, "execute", too_many)
        with pytest.raises(AnalyticsUnavailable, match="exceeds"):
            await worker.once()
        assert world.progress == previous and not world.open

        class Invalid:
            async def query(self, *_args):
                return [{"domain": "job", "id": identity(1), "revision": 1}]

        with pytest.raises(AnalyticsUnavailable, match="target page"):
            await refresh._metadata_holes(Invalid())

    asyncio.run(run())


def test_orphan_holes_use_start_fence_and_preserve_a_newer_live_race(monkeypatch):
    world = World(monkeypatch)
    world.data[("feed", identity(1))] = {
        "domain": "feed",
        "id": identity(1),
        "revision": 1,
        "created_at": None,
    }
    world.data[("feed", identity(2))] = {
        "domain": "feed",
        "id": identity(2),
        "revision": 1,
        "created_at": None,
    }

    async def run():
        worker = world.worker()
        while world.progress is None or json.loads(world.progress["cursor"])["phase"] < 3:
            await worker.once()
        start = json.loads(world.progress["cursor"])["start_revision"]

        def raced():
            world.after_holes = None
            world.data[("feed", identity(2))] = {
                "domain": "feed",
                "id": identity(2),
                "revision": start + 1,
                "created_at": NOW,
                "occurred_at": None,
            }

        world.after_holes = raced
        assert await worker.once()
        assert world.data[("feed", identity(1))]["deleted"] == 1
        assert world.data[("feed", identity(1))]["revision"] == start
        assert world.data[("feed", identity(2))]["revision"] == start + 1
        assert not world.data[("feed", identity(2))].get("deleted")
        await finish(worker)
        assert await refresh.audience_metadata_ready(world)

    asyncio.run(run())


def test_new_low_uuid_after_source_cursor_is_protected_until_next_pass(monkeypatch):
    world = World(monkeypatch)
    world.sources["user_accounts"] = [{"id": identity(2), "created_at": NOW}]

    async def run():
        worker = world.worker()
        await worker.once()
        await worker.once()
        start = json.loads(world.progress["cursor"])["start_revision"]
        world.sources["user_accounts"].append({"id": identity(0), "created_at": NOW})
        world.data[("feed", identity(0))] = {
            "domain": "feed",
            "id": identity(0),
            "revision": world.revision + 1,
            "created_at": None,
        }
        world.revision += 1
        while not worker.complete:
            await worker.once()
        assert world.data[("feed", identity(0))]["revision"] > start
        assert not world.data[("feed", identity(0))].get("deleted")
        assert not await refresh.audience_metadata_ready(world)
        await finish(worker)
        assert await refresh.audience_metadata_ready(world)
        assert world.data[("feed", identity(0))]["created_at"] == NOW

    asyncio.run(run())


def test_mixed_writer_batches_keep_owner_out_of_pipeline_and_real_dates_in_user_rows():
    class StrictSink:
        def __init__(self):
            self.rows = []

        async def insert(self, table, rows):
            assert all(set(row) == set(rows[0]) for row in rows)
            assert all(("user_id" in row) == (table == "analytics_user_events") for row in rows)
            self.rows.extend(rows)

    sink = StrictSink()
    changes = [
        {
            "source_table": "articles",
            "record": {"id": identity(10)},
            "publication": {"article_id": identity(10), "published_at": NOW},
        },
        {
            "source_table": "user_accounts",
            "record": {"id": identity(1), "created_at": NOW},
            "feed": {
                "user_id": identity(1),
                "created_at": NOW - timedelta(days=30),
                "computed_at": NOW,
            },
        },
        {
            "source_table": "user_topics",
            "record": {
                "user_id": identity(1),
                "topic_id": identity(2),
                "created_at": NOW - timedelta(days=3),
            },
        },
        {
            "source_table": "user_interests",
            "record": {
                "user_id": identity(1),
                "topic_id": identity(3),
                "computed_at": NOW,
                "reason": "related_topic",
            },
        },
        {
            "source_table": "ingestion_jobs",
            "record": {
                "id": identity(20),
                "status": "succeeded",
                "attempts": 1,
                "finished_at": NOW,
            },
        },
    ]
    asyncio.run(refresh.DirectOverviewAnalytics(sink).publish(changes, revision=11))
    assert {row["domain"] for row in sink.rows} == {
        "publication",
        "account",
        "feed",
        "membership",
        "job",
        "queue",
    }
    feed = next(row for row in sink.rows if row["domain"] == "feed")
    assert feed["occurred_at"] == NOW and feed["created_at"] == NOW - timedelta(days=30)
    follows = next(row for row in sink.rows if row["task"] == "follow")
    assert follows["occurred_at"] == NOW - timedelta(days=3)
    inferred = next(row for row in sink.rows if row["task"] == "inferred")
    assert inferred["occurred_at"] == NOW and inferred["user_id"] == identity(1)


def test_changed_interest_uses_bounded_feed_metadata_and_state_refresh_does_not_fan_out(
    monkeypatch,
):
    async def feed_rows(_connection, users):
        assert users == {identity(1)}
        return {identity(1): {"user_id": identity(1), "created_at": NOW, "computed_at": NOW}}

    monkeypatch.setattr(analytics_producer, "_feed_rows", feed_rows)
    interest = {"user_id": identity(1), "topic_id": identity(2), "reason": "related_topic"}

    async def run():
        changes = await analytics_producer.hydrate_changes(
            None, [{"source_table": "user_interests", "record": interest}]
        )
        assert changes[0]["record"]["computed_at"] == NOW
        assert "computed_at" not in interest
        refreshed = await analytics_producer.hydrate_changes(
            None,
            [{"source_table": "user_recommendation_states", "record": {"user_id": identity(1)}}],
        )
        assert all(change["source_table"] != "user_interests" for change in refreshed)

    asyncio.run(run())
