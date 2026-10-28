"""Direct usage writes are content-free and cannot retry successful AI work."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from devfeed_core import inference_usage


def invocation():
    return {
        "id": uuid.uuid4(),
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, tzinfo=UTC),
        "operation": "source_relevance",
        "job_id": None,
        "attempt": 1,
        "reason": "private reason",
        "model": "test-model",
        "reasoning_effort": None,
        "request_hash": "private hash",
        "status": "returned",
        "tokens": {"inputTokens": 50, "outputTokens": 20, "private": "secret"},
        "web_searches": 2,
        "duration_ms": 10,
    }


def test_usage_is_written_directly_without_content_or_request_metadata():
    async def run():
        client = AsyncMock()
        values = invocation()
        await inference_usage.record_call(values, client=client)
        client.insert.assert_awaited_once()
        table, rows = client.insert.await_args.args
        assert table == "analytics_inference_events"
        assert rows[0]["input_tokens"] == 50 and rows[0]["output_tokens"] == 20
        assert not {"prompt", "request_hash", "tokens", "response"} & rows[0].keys()
        assert rows[0]["reason"] is None
        assert "private reason" not in str(rows) and "secret" not in str(rows)

    asyncio.run(run())


def test_clickhouse_failure_is_nonfatal_and_logs_only_invocation_identity(caplog):
    async def run():
        client = AsyncMock()
        client.insert.side_effect = RuntimeError("private connection string")
        values = invocation()
        await inference_usage.record_call(values, client=client)
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.inference_id == str(values["id"]) and event.exc_info is None
        assert "private connection string" not in caplog.text

    asyncio.run(run())
