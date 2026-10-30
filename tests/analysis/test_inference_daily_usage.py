"""Direct native inference accounting preserves identity, counters and query bounds."""

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.config import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 18, 19, tzinfo=UTC)


def invocation(at, **changes):
    return {
        "id": uuid.uuid4(),
        "started_at": at,
        "operation": "partner_qualification",
        "model": "model-a",
        "reasoning_effort": "low",
        "status": "returned",
        "tokens": {
            "inputTokens": 100,
            "cachedInputTokens": 200,
            "outputTokens": 20,
            "reasoningOutputTokens": 50,
        },
        "web_searches": 1,
        "duration_ms": 500,
        **changes,
    }


def native(read):
    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for native inference accounting")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Analytical fixture database must end with _test")
    configured = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://fixture@database.invalid/fixture_test",
        redis_url="redis://redis.invalid/15",
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )

    async def run():
        name = f"inference_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{name}`")
            try:
                async with AnalyticsClient(
                    configured.model_copy(update={"clickhouse_database": name})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    store = InferenceStore(client)
                    await client.initialize()
                    return await read(client, store)
            finally:
                await admin.execute(f"DROP DATABASE `{name}` SYNC")

    return asyncio.run(run())


def test_ambiguous_ack_replay_and_late_running_row_preserve_native_report_totals():
    from devfeed_core.inference_usage import record_call

    async def read(client, store):
        completed = invocation(NOW)
        running = completed | {"status": "running", "tokens": {}}
        await store.write([running])

        class LostAck:
            def __init__(self):
                self.failed = False
                self.settings = client.settings
                self.observations = []

            async def insert(self, table, rows):
                self.observations.append(rows)
                await client.insert(table, rows)
                if not self.failed:
                    self.failed = True
                    raise AnalyticsUnavailable("Acknowledgement was lost")

        uncertain = InferenceStore(LostAck())
        with pytest.raises(AnalyticsUnavailable):
            await uncertain.write([completed])  # The real server already ACKed.
        await uncertain.write([completed])
        await store.write([running])  # An older revision may arrive after replay.
        retrying = LostAck()
        stopped, ticks = asyncio.Event(), []

        async def heartbeat():
            while not stopped.is_set():
                ticks.append(True)
                await asyncio.sleep(0.005)

        beating = asyncio.create_task(heartbeat())
        try:
            await record_call(completed, client=retrying)
        finally:
            stopped.set()
            await beating
        assert len(ticks) >= 10
        assert len(retrying.observations) == 2
        assert retrying.observations[0] == retrying.observations[1]
        rows = await client.query("SELECT * FROM analytics_inference_events FINAL")
        assert len(rows) == 1 and rows[0]["revision"] == 1
        assert rows[0]["status"] == "returned"
        totals = await store.aggregate(NOW - timedelta(seconds=1), NOW + timedelta(seconds=1))
        summary = next(row for row in totals if row["dimension"] == "summary")
        assert summary["calls"] == 1
        assert summary["input_tokens"] == summary["cached_input_tokens"] == 100
        assert summary["output_tokens"] == summary["reasoning_tokens"] == 20
        assert summary["web_searches"] == 1

    native(read)


def expected(records, start, end):
    def number(value):
        return int(value) if re.fullmatch(r"[0-9]{1,12}", str(value)) else 0

    result = dict(
        calls=0,
        failed=0,
        unreported=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        web_searches=0,
    )
    for row in records:
        if not start <= row["started_at"] < end:
            continue
        tokens = row.get("tokens") or {}
        incoming, outgoing = number(tokens.get("inputTokens")), number(tokens.get("outputTokens"))
        result["calls"] += 1
        result["failed"] += row["status"] == "failed"
        result["unreported"] += incoming + outgoing == 0
        result["input_tokens"] += incoming
        result["cached_input_tokens"] += min(incoming, number(tokens.get("cachedInputTokens")))
        result["output_tokens"] += outgoing
        result["reasoning_tokens"] += min(outgoing, number(tokens.get("reasoningOutputTokens")))
        result["web_searches"] += max(row.get("web_searches") or 0, 0)
    return result


@pytest.mark.parametrize("days", [1, 30, 90])
def test_native_numeric_totals_and_half_open_window_boundaries(days):
    start, end = NOW - timedelta(days=days), NOW
    values = [
        0,
        42,
        "00042",
        "999999999999",
        None,
        True,
        False,
        -1,
        1.5,
        "",
        " 1",
        "-1",
        "1.0",
        "1000000000000",
        "١٢",
        [],
        {},
    ]
    rows = [
        invocation(
            start + timedelta(hours=1),
            tokens={key: value},
            model=[None, "Unknown model", "", "model-a"][index % 4],
            status=["returned", "failed", "running", "cancelled"][index % 4],
        )
        for index, (key, value) in enumerate(
            (key, value)
            for key in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens")
            for value in values
        )
    ]
    rows.extend(
        invocation(at, operation="boundary")
        for at in (
            start - timedelta(microseconds=1),
            start,
            end - timedelta(microseconds=1),
            end,
        )
    )

    async def check(_client, store):
        await store.write(rows)
        summary = next(
            row for row in await store.aggregate(start, end) if row["dimension"] == "summary"
        )
        assert {key: int(summary[key]) for key in expected(rows, start, end)} == expected(
            rows, start, end
        )
        assert summary["percentiles"] == [500, 500]

    native(check)


def test_terminal_replays_and_concurrent_starts_do_not_duplicate_completed_invocations():
    row = invocation(NOW - timedelta(days=1), duration_ms=-1, web_searches=-2)
    terminal = {**row, "status": "returned"}
    running = {**row, "status": "running", "tokens": {}}

    async def check(client, store):
        await asyncio.gather(store.write([running]), store.write([terminal]))
        await store.write([terminal, running])
        observed = await client.query(
            "SELECT status,duration_ms,has_duration,web_searches "
            "FROM analytics_inference_events FINAL"
        )
        assert observed == [
            {"status": "returned", "duration_ms": 0, "has_duration": 0, "web_searches": 0}
        ]
        summary = next(
            row
            for row in await store.aggregate(NOW - timedelta(days=2), NOW)
            if row["dimension"] == "summary"
        )
        assert summary["calls"] == 1 and summary["input_tokens"] == 100

    native(check)


def test_unknown_display_labels_merge_and_keep_raw_identity_without_private_payloads():
    rows = [
        invocation(
            NOW,
            model=model,
            reasoning_effort=effort,
            tokens={"inputTokens": index, "outputTokens": index + 3},
            request_hash="private",
            prompt="private",
            reason="private",
        )
        for index, (model, effort) in enumerate(
            [(None, None), ("", ""), ("Unknown model", "Inherited / unknown")], start=1
        )
    ]

    async def check(client, store):
        await store.write(rows)
        aggregate = await store.aggregate(NOW, NOW + timedelta(seconds=1))
        models = [row for row in aggregate if row["dimension"] == "models"]
        assert len(models) == 1 and models[0]["name"] == "Unknown model"
        assert models[0]["calls"] == 3 and models[0]["input_tokens"] == 6
        raw = await client.query("SELECT * FROM analytics_inference_events FINAL")
        assert len(raw) == 3 and len({row["id"] for row in raw}) == 3
        assert "private" not in str(raw)

    native(check)
