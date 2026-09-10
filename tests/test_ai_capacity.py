import asyncio
from datetime import timedelta

import pytest
from devfeed_aggregator.codex_client import AnalysisError, CodexClient, turn_error
from devfeed_core.analysis import fail_analysis
from devfeed_core.models import ArticleAnalysisJob, utcnow
from test_codex_client import WebSocket, settings


def test_usage_events_are_cumulative_scoped_and_not_summed_twice():
    event = {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "tokenUsage": {
                "total": {
                    "inputTokens": 100,
                    "cachedInputTokens": 60,
                    "outputTokens": 20,
                    "totalTokens": 120,
                }
            },
        },
    }
    other = {
        "method": event["method"],
        "params": {
            **event["params"],
            "threadId": "other",
            "tokenUsage": {"total": {"totalTokens": 9999}},
        },
    }
    ws = WebSocket(extra=[event, event, other])
    client = CodexClient(settings(), connector=lambda *a, **kw: ws)
    client.complete("Analyze", {"type": "object"})
    assert client.usage == {
        "inputTokens": 100,
        "cachedInputTokens": 60,
        "outputTokens": 20,
        "totalTokens": 120,
    }


@pytest.mark.parametrize(
    "info,code",
    [
        ("UsageLimitExceeded", "codex_usage_limit"),
        ("usageLimitExceeded", "codex_usage_limit"),
        ({"httpConnectionFailed": {"httpStatusCode": 429}}, "codex_rate_limited"),
        ({"HttpConnectionFailed": {"httpStatusCode": 500}}, "codex_turn_failed"),
        (None, "codex_turn_failed"),
    ],
)
def test_only_structured_provider_errors_control_capacity(info, code):
    assert turn_error({"codexErrorInfo": info, "message": "secret 429 limit reached"}) == code


def test_failed_turn_preserves_safe_capacity_error():
    ws = WebSocket(
        status="failed",
        extra=[
            {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "error": {"codexErrorInfo": "UsageLimitExceeded", "message": "private"},
                },
            }
        ],
    )
    client = CodexClient(settings(), connector=lambda *a, **kw: ws)
    with pytest.raises(AnalysisError, match="^codex_usage_limit$"):
        client.complete("Analyze", {"type": "object"})


def test_capacity_deferrals_do_not_exhaust_normal_retry_budget():
    job = ArticleAnalysisJob(attempts=8, usage={"capacity_deferrals": 7})
    fail_analysis(job, "codex_usage_limit", retry_after=600)
    assert job.status == "queued" and job.usage["capacity_deferrals"] == 8
    assert job.available_at > utcnow() + timedelta(seconds=590)
    job.attempts = 9
    fail_analysis(job, "codex_timeout")
    assert job.status == "queued"
    job.attempts = 11
    fail_analysis(job, "codex_timeout")
    assert job.status == "failed"


def test_rpc_capacity_error_preserves_safe_code():
    ws = WebSocket()
    ws.messages.append(
        {"id": 9, "error": {"data": {"codexErrorInfo": "UsageLimitExceeded"}, "message": "private"}}
    )
    client = CodexClient(settings())
    with pytest.raises(AnalysisError, match="^codex_usage_limit$"):
        asyncio.run(client.request(ws, 9, "test", {}))
