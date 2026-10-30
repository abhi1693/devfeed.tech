"""Direct usage writes are content-free and cannot retry successful AI work."""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectionError, ClientResponseError
from clickhouse_connect.driver.exceptions import DatabaseError, OperationalError
from devfeed_core import inference_usage
from devfeed_core.analytics import AnalyticsUnavailable


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


def client(timeout=30):
    return SimpleNamespace(
        insert=AsyncMock(), settings=SimpleNamespace(clickhouse_timeout_seconds=timeout)
    )


def test_usage_is_written_directly_without_content_or_request_metadata():
    async def run():
        current = client()
        values = invocation()
        await inference_usage.record_call(values, client=current)
        current.insert.assert_awaited_once()
        table, rows = current.insert.await_args.args
        assert table == "analytics_inference_events"
        assert rows[0]["input_tokens"] == 50 and rows[0]["output_tokens"] == 20
        assert not {"prompt", "request_hash", "tokens", "response"} & rows[0].keys()
        assert rows[0]["reason"] is None
        assert "private reason" not in str(rows) and "secret" not in str(rows)

    asyncio.run(run())


def test_clickhouse_failure_is_nonfatal_and_logs_only_invocation_identity(caplog):
    async def run():
        current = client()
        current.insert.side_effect = RuntimeError("private connection string")
        values = invocation()
        await inference_usage.record_call(values, client=current)
        current.insert.assert_awaited_once()
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.inference_id == str(values["id"]) and event.exc_info is None
        assert "private connection string" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    [
        AnalyticsUnavailable("offline"),
        ClientConnectionError("private address"),
        ClientResponseError(None, (), status=503, message="private service"),
        TimeoutError("private timeout"),
        OperationalError("private disconnect"),
        DatabaseError("private query", code=202),
        DatabaseError("private allocation", code=241),
    ],
)
def test_transient_observation_retry_preserves_identity_and_event_values(monkeypatch, failure):
    monkeypatch.setattr(inference_usage, "PERSISTENCE_DELAYS", (0, 0.001, 0.001))

    async def run():
        current = client()
        current.insert.side_effect = [failure, None]
        values = invocation()
        await inference_usage.record_call(values, client=current)
        assert current.insert.await_count == 2
        first, second = [call.args for call in current.insert.await_args_list]
        assert first == second
        assert first[1][0]["id"] == str(values["id"])
        assert first[1][0]["started_at"] == values["started_at"]

    asyncio.run(run())


@pytest.mark.parametrize("code", [60, 497])
def test_permanent_clickhouse_errors_are_not_retried_and_logs_are_sanitized(caplog, code):
    async def run():
        current = client()
        failure = AnalyticsUnavailable("Unavailable")
        failure.__cause__ = DatabaseError("private connection and SQL", code=code)
        current.insert.side_effect = failure
        await inference_usage.record_call(invocation(), client=current)
        current.insert.assert_awaited_once()
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.attempt == 1 and event.clickhouse_code == code
        assert event.error_type == "AnalyticsUnavailable" and event.cause_type == "DatabaseError"
        assert "private connection" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403])
def test_permanent_http_authentication_errors_are_not_retried(caplog, status):
    async def run():
        current = client()
        current.insert.side_effect = ClientResponseError(
            None, (), status=status, message="private authentication details"
        )
        await inference_usage.record_call(invocation(), client=current)
        current.insert.assert_awaited_once()
        assert "private authentication" not in caplog.text

    asyncio.run(run())


def test_exhausted_retry_has_three_attempts_and_one_safe_failure(monkeypatch, caplog):
    monkeypatch.setattr(inference_usage, "PERSISTENCE_DELAYS", (0, 0.001, 0.001))

    async def run():
        current = client()
        failure = AnalyticsUnavailable("Unavailable")
        failure.__cause__ = DatabaseError("private INSERT", code=202)
        current.insert.side_effect = failure
        await inference_usage.record_call(invocation(), client=current)
        assert current.insert.await_count == 3
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.attempt == 3 and event.clickhouse_code == 202
        assert "private INSERT" not in caplog.text

    asyncio.run(run())


def test_retry_freezes_the_original_observation_before_the_first_await(monkeypatch):
    monkeypatch.setattr(inference_usage, "PERSISTENCE_DELAYS", (0, 0.001, 0.001))

    async def run():
        current, values, seen = client(), invocation(), []

        async def insert(table, rows):
            seen.append((table, rows))
            if len(seen) == 1:
                values["tokens"]["inputTokens"] = 999
                values["started_at"] = datetime(2026, 2, 1, tzinfo=UTC)
                raise AnalyticsUnavailable("Transient")

        current.insert.side_effect = insert
        await inference_usage.record_call(values, client=current)
        assert len(seen) == 2 and seen[0] == seen[1]
        assert seen[1][1][0]["input_tokens"] == 50
        assert seen[1][1][0]["started_at"] == datetime(2026, 1, 1, tzinfo=UTC)

    asyncio.run(run())


def test_retry_deadline_keeps_the_event_loop_responsive_and_bounds_total_wait(caplog):
    async def run():
        current = client(timeout=0.03)
        entered = asyncio.Event()

        async def blocked(*_args):
            entered.set()
            await asyncio.Event().wait()

        current.insert.side_effect = blocked
        writing = asyncio.create_task(inference_usage.record_call(invocation(), client=current))
        await asyncio.wait_for(entered.wait(), 1)
        for _ in range(5):
            await asyncio.sleep(0.001)
            assert not writing.done()
        await asyncio.wait_for(writing, 0.5)
        current.insert.assert_awaited_once()
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.error_type == "TimeoutError" and event.attempt == 1

    asyncio.run(run())


def test_external_cancellation_propagates_without_a_persistence_failure(caplog):
    async def run():
        current = client()
        entered = asyncio.Event()

        async def blocked(*_args):
            entered.set()
            await asyncio.Event().wait()

        current.insert.side_effect = blocked
        writing = asyncio.create_task(inference_usage.record_call(invocation(), client=current))
        await asyncio.wait_for(entered.wait(), 1)
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writing
        assert not [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]

    asyncio.run(run())


def test_overall_deadline_includes_backoff_without_counting_an_unstarted_retry(caplog):
    async def run():
        current = client(timeout=0.03)
        current.insert.side_effect = AnalyticsUnavailable("Unavailable")
        await asyncio.wait_for(inference_usage.record_call(invocation(), client=current), 0.5)
        current.insert.assert_awaited_once()
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.error_type == "TimeoutError" and event.attempt == 1

    asyncio.run(run())


def test_invalid_identity_is_nonfatal_and_never_copied_into_logs(caplog):
    async def run():
        current = client()
        await inference_usage.record_call(invocation() | {"id": "private identity"}, client=current)
        current.insert.assert_not_awaited()
        [event] = [r for r in caplog.records if r.message == "inference_usage_persistence_failed"]
        assert event.inference_id == "invalid" and event.attempt == 1
        assert "private identity" not in caplog.text

    asyncio.run(run())
