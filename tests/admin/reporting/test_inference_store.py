"""Inference observations are content-free, bounded and replay-safe."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_inference_store import MAX_BATCH_SIZE, InferenceStore


class Client:
    def __init__(self):
        self.rows = []
        self.queries = []
        self.failed = False

    async def insert(self, table, rows):
        if self.failed:
            raise AnalyticsUnavailable("offline")
        assert table == "analytics_inference_events"
        self.rows.extend(rows)

    async def query(self, statement, parameters):
        self.queries.append((statement, parameters))
        return [{"calls": 2}] if "SELECT count()" in statement else [{"dimension": "summary"}]


def invocation():
    return {
        "id": uuid.uuid4(),
        "started_at": datetime(2026, 1, 1, 1, 30, tzinfo=UTC),
        "status": "running",
        "operation": "relationship_research",
        "model": None,
        "reasoning_effort": None,
        "tokens": {
            "inputTokens": 100,
            "cachedInputTokens": 200,
            "cacheWriteInputTokens": 10,
            "outputTokens": "20",
            "reasoningOutputTokens": "30",
            "request": "secret",
        },
        "duration_ms": 0,
        "web_searches": 1,
        "reason": "private reason",
        "prompt": "secret prompt",
        "request_hash": "secret hash",
    }


def test_observations_use_stable_identity_terminal_revision_and_never_store_private_data():
    async def run():
        client = Client()
        store = InferenceStore(client)
        item = invocation()
        assert await store.write([item]) == 1
        assert await store.write([{**item, "status": "returned"}, item]) == 2
        start, terminal, retry = client.rows
        assert (start["id"], start["started_at"]) == (terminal["id"], terminal["started_at"])
        assert (start["revision"], terminal["revision"], retry["revision"]) == (0, 1, 0)
        assert (start["input_tokens"], start["cached_input_tokens"]) == (100, 100)
        assert (start["output_tokens"], start["reasoning_tokens"]) == (20, 20)
        assert start["reported_cached_input_tokens"] == 200
        assert start["cache_write_input_tokens"] == 10 and start["has_input_tokens"] == 1
        assert start["reason"] is None
        assert "secret" not in str(start)
        assert start["model"] == "Unknown model" and start["effort"] == "Inherited / unknown"

    asyncio.run(run())


def test_batch_is_validated_before_writing_and_outage_is_explicit():
    async def run():
        client = Client()
        store = InferenceStore(client)
        item = invocation()
        with pytest.raises(ValueError, match="batch size"):
            await store.write([item] * (MAX_BATCH_SIZE + 1))
        with pytest.raises(ValueError, match="identity"):
            await store.write([item, {**item, "id": "bad"}])
        assert not client.rows
        client.failed = True
        with pytest.raises(AnalyticsUnavailable):
            await store.write([item])
        assert not client.rows

    asyncio.run(run())


def test_direct_reads_have_one_query_for_all_report_dimensions_and_reject_sql_injection():
    async def run():
        client = Client()
        store = InferenceStore(client)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        rows = await store.aggregate(start, end, granularity="hour")
        assert rows == [{"dimension": "summary"}]
        assert len(client.queries) == 1
        assert "FINAL" in client.queries[0][0] and "GROUPING SETS" in client.queries[0][0]
        assert await store.relationship_count(start) == 2
        with pytest.raises(ValueError):
            await store.aggregate(start, end, granularity="day); DROP TABLE x")
        with pytest.raises(ValueError):
            await store.aggregate(end, start)

    asyncio.run(run())


def test_relationship_quota_serializes_reservations_and_rejects_retries():
    from contextlib import asynccontextmanager

    from devfeed_core.analytics_inference_store import RelationshipQuota

    class Store:
        def __init__(self):
            self.rows = []
            self.offline = False

        async def relationship_count(self, _since):
            return len(self.rows)

        async def exists(self, identity, started):
            return any(
                str(row["id"]) == identity and row["started_at"] == started for row in self.rows
            )

        async def write(self, rows):
            if self.offline:
                raise AnalyticsUnavailable("offline")
            self.rows.extend(rows)
            await asyncio.sleep(0)

    class Connections:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.fail_commit = False

        @asynccontextmanager
        async def connection(self):
            held = False
            owner = self

            class Connection:
                @asynccontextmanager
                async def transaction(self):
                    nonlocal held
                    try:
                        yield
                        if owner.fail_commit:
                            raise RuntimeError("lost database connection")
                    finally:
                        if held:
                            owner.lock.release()

                async def execute(self, statement, _params):
                    nonlocal held
                    if "pg_advisory_xact_lock" in statement:
                        await owner.lock.acquire()
                        held = True

            yield Connection()

    async def run():
        store, connections = Store(), Connections()
        quota = RelationshipQuota(store, connections.connection, timeout_seconds=1)
        items = [invocation() for _ in range(8)]
        accepted = await asyncio.gather(*(quota.admit(item, 3) for item in items))
        assert sum(accepted) == len(store.rows) == 3
        assert not await quota.admit(store.rows[0], 10)
        store.offline = True
        with pytest.raises(AnalyticsUnavailable):
            await quota.admit(invocation(), 10)
        store.offline = False
        connections.fail_commit = True
        with pytest.raises(RuntimeError, match="lost database"):
            await quota.admit(invocation(), 10)
        assert len(store.rows) == 4  # A conservative reservation, never an authorized call.

    asyncio.run(run())


def test_relationship_quota_rejects_nonrunning_or_unrelated_invocations():
    from devfeed_core.analytics_inference_store import RelationshipQuota

    async def run():
        quota = RelationshipQuota(None, lambda: None)
        for record, limit in (
            ({**invocation(), "status": "returned"}, 1),
            ({**invocation(), "operation": "article_analysis"}, 1),
            (invocation(), -1),
        ):
            with pytest.raises(ValueError):
                await quota.admit(record, limit)

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"started_at": "2026-01-01T00:00:00Z"},
        {"started_at": datetime(2026, 1, 1)},
    ],
)
def test_imported_iso_and_naive_timestamps_are_normalized_to_utc(changes):
    async def run():
        client = Client()
        await InferenceStore(client).write([invocation() | changes])
        assert client.rows[0]["started_at"] == datetime(2026, 1, 1, tzinfo=UTC)

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"started_at": "invalid"},
        {"started_at": None},
        {"status": 123},
        {"operation": "x" * 201},
        {"model": 123},
        {"reasoning_effort": "x" * 201},
        {"tokens": [1]},
        {"web_searches": "invalid"},
        {"duration_ms": {}},
        {"web_searches": 2**64},
        {"duration_ms": 2**64},
    ],
)
def test_malformed_inference_telemetry_cannot_partially_write_a_batch(changes):
    async def run():
        client = Client()
        with pytest.raises(ValueError):
            await InferenceStore(client).write([invocation(), invocation() | changes])
        assert client.rows == []

    asyncio.run(run())


def test_empty_batches_and_nonobject_observations_do_not_open_transport():
    async def run():
        client = Client()
        store = InferenceStore(client)
        assert await store.write([]) == 0
        with pytest.raises(ValueError, match="object"):
            await store.write([None])
        with pytest.raises(ValueError, match="batch"):
            await store.write(None)
        assert not client.rows

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["relationship_count", "exists"])
@pytest.mark.parametrize(
    "reply", [[], [{"calls": True}], [{"calls": "1"}], [{"calls": 1}, {"calls": 1}]]
)
def test_quota_reads_fail_closed_on_ambiguous_or_malformed_accounting(operation, reply):
    class Invalid(Client):
        async def query(self, *_args):
            return reply

    async def run():
        store = InferenceStore(Invalid())
        arguments = (
            (uuid.uuid4(), datetime.now(UTC)) if operation == "exists" else (datetime.now(UTC),)
        )
        with pytest.raises(RuntimeError, match="count"):
            await getattr(store, operation)(*arguments)

    asyncio.run(run())


def test_exact_invocation_lookup_and_schema_initialization_are_bounded():
    class Storage(Client):
        async def execute(self, statement):
            self.schema = statement

    async def run():
        client = Storage()
        store = InferenceStore(client)
        await store.initialize()
        assert "analytics_inference_events" in client.schema
        item = invocation()
        assert await store.exists(item["id"], item["started_at"])
        assert client.queries[-1][1] == dict(id=str(item["id"]), started=item["started_at"])
        with pytest.raises(ValueError, match="identity"):
            await store.exists("invalid", item["started_at"])
        assert len(client.queries) == 1

    asyncio.run(run())


@pytest.mark.parametrize("factory,timeout", [(None, 30), (lambda: None, 0), (lambda: None, 301)])
def test_relationship_reservation_rejects_invalid_coordination_configuration(factory, timeout):
    from devfeed_core.analytics_inference_store import RelationshipQuota

    with pytest.raises(ValueError, match="configuration"):
        RelationshipQuota(None, factory, timeout_seconds=timeout)
