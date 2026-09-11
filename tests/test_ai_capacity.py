import asyncio
import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.codex_client import AnalysisError, CodexClient, turn_error
from devfeed_core.analysis import fail_analysis
from devfeed_core.models import (
    ArticleAnalysisJob,
    ResearchVerificationJob,
    TopicAnalysisJob,
    utcnow,
)
from devfeed_core.research_verification import fail_verification
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


@pytest.mark.parametrize(
    "model,fail",
    [
        (ArticleAnalysisJob, fail_analysis),
        (TopicAnalysisJob, fail_analysis),
        (ResearchVerificationJob, fail_verification),
    ],
)
@pytest.mark.parametrize(
    "reason,status",
    [
        ("ai_not_configured", "failed"),
        ("unexpected_tool_execution", "failed"),
        ("unexpected_server_request", "failed"),
        ("codex_timeout", "queued"),
        ("codex_usage_limit", "queued"),
    ],
)
def test_all_ai_pipelines_share_terminal_errors_and_keep_their_backoff(model, fail, reason, status):
    before = utcnow()
    job = model(
        status="running", attempts=1, lease_token=uuid.uuid4(), dispatched_at=before, usage={}
    )
    fail(job, reason, retry_after=600)
    assert job.status == status and job.error == reason
    assert job.lease_token is None and job.lease_until is None and job.dispatched_at is None
    assert job.attempts == 1
    if status == "failed":
        assert job.finished_at >= before
    else:
        seconds = 600 if model is ResearchVerificationJob or reason == "codex_usage_limit" else 30
        assert before + timedelta(seconds=seconds) <= job.available_at
        assert job.available_at <= utcnow() + timedelta(seconds=seconds)
        assert job.finished_at is None
    assert job.usage.get("capacity_deferrals", 0) == int(reason == "codex_usage_limit")


def test_caller_can_disable_retries_for_an_otherwise_transient_analysis_error():
    job = ArticleAnalysisJob(status="running", attempts=1, usage={})
    fail_analysis(job, "analysis_dependency_failure", retryable=False)
    assert job.status == "failed" and job.finished_at is not None


def test_rpc_capacity_error_preserves_safe_code():
    ws = WebSocket()
    ws.messages.append(
        {"id": 9, "error": {"data": {"codexErrorInfo": "UsageLimitExceeded"}, "message": "private"}}
    )
    client = CodexClient(settings())
    with pytest.raises(AnalysisError, match="^codex_usage_limit$"):
        asyncio.run(client.request(ws, 9, "test", {}))
