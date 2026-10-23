"""Provider WebSocket RPC, quotas, and bounded protocol failure contracts."""

import asyncio
import json
from collections import deque
from unittest.mock import Mock

import pytest
from devfeed_aggregator import codex_client as provider
from devfeed_core import quota_pacing, topic_decision_budget
from websockets.exceptions import ConnectionClosedError, InvalidHandshake
from websockets.frames import Close

from tests.analysis.test_codex_client import WebSocket, settings


@pytest.fixture(autouse=True)
def no_quota_dependency(monkeypatch):
    monkeypatch.setattr(quota_pacing, "current_pause_reason", lambda _: None)


def client(socket, **changes):
    ledger = []
    instance = provider.CodexClient(
        settings(**changes), connector=lambda *_a, **_kw: socket, usage_recorder=ledger.append
    )
    return instance, ledger


def test_disabled_readiness_never_opens_a_transport_or_reserves_inference():
    connect, ledger = Mock(), Mock()
    instance = provider.CodexClient(
        settings(ai_enabled=False), connector=connect, usage_recorder=ledger
    )
    assert instance.readiness() == "ai_not_configured"
    connect.assert_not_called()
    ledger.assert_not_called()


def test_quota_read_uses_only_account_rpc_and_returns_the_unmodified_quota_document():
    response = {
        "rateLimits": {"primary": {"usedPercent": 42, "resetsAt": 1800000000}, "secondary": None}
    }

    class QuotaSocket(WebSocket):
        async def send(self, raw):
            message = json.loads(raw)
            if message.get("method") == "account/rateLimits/read":
                self.sent.append(message)
                self.messages.append({"id": message["id"], "result": response})
            else:
                await super().send(raw)

    socket = QuotaSocket()
    instance, ledger = client(socket)
    assert instance.read_quota() == response
    assert [item["method"] for item in socket.sent] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    assert socket.sent[-1]["params"] == {} and ledger == []


@pytest.mark.parametrize("admitted", [False, True])
def test_bounded_relationship_call_reserves_before_transport_and_records_admitted_usage(
    monkeypatch, admitted
):
    socket = WebSocket()
    instance, ledger = client(socket, ai_bounded_topics_enabled=True)
    instance.operation = "relationship_verification"
    admit = Mock(return_value=admitted)
    monkeypatch.setattr(topic_decision_budget, "admit_relationship_call", admit)
    if admitted:
        assert instance.complete(
            "Check the exact relationship", {"type": "object"}, allow_web_search=True
        ) == {"answer": "ok"}
        assert len(ledger) == 1 and ledger[0]["status"] == "returned"
    else:
        with pytest.raises(provider.AnalysisError, match="relationship_budget_deferred"):
            instance.complete(
                "Check the exact relationship", {"type": "object"}, allow_web_search=True
            )
        assert socket.sent == [] and ledger == []
    values = admit.call_args.args[0]
    assert values["operation"] == "relationship_verification" and values["status"] == "running"
    assert len(values["request_hash"]) == 64 and values["tokens"] == {}


def test_server_configuration_must_be_an_object_before_any_thread_can_start():
    class InvalidConfiguration(WebSocket):
        async def send(self, raw):
            message = json.loads(raw)
            if message.get("method") == "config/read":
                self.sent.append(message)
                self.messages.append({"id": message["id"], "result": {"config": None}})
            else:
                await super().send(raw)

    socket = InvalidConfiguration()
    instance, ledger = client(socket)
    with pytest.raises(provider.AnalysisError, match="invalid_server_configuration"):
        instance.complete("Analyze supplied text", {"type": "object"})
    assert not any(item.get("method") in {"thread/start", "turn/start"} for item in socket.sent)
    assert ledger[-1]["status"] == "failed"


class RpcSocket:
    def __init__(self, *messages):
        self.messages, self.sent = deque(messages), []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self.messages.popleft())


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            {
                "code": -32000,
                "message": "private upstream details",
                "data": {"codexErrorInfo": {"Http": {"httpStatusCode": 429}}},
            },
            "codex_rate_limited",
        ),
        ("private upstream details", "codex_request_failed"),
    ],
)
def test_rpc_errors_map_provider_capacity_and_protocol_failures_without_echoing_private_text(
    error, reason
):
    socket = RpcSocket({"id": 23, "error": error})
    instance, _ = client(socket)
    with pytest.raises(provider.AnalysisError) as caught:
        asyncio.run(instance.request(socket, 23, "account/read", {}))
    assert str(caught.value) == reason and "private" not in str(caught.value)


def test_rpc_requires_an_object_reply_and_bounds_unrelated_notifications():
    socket = RpcSocket({"id": 23, "result": []})
    instance, _ = client(socket)
    with pytest.raises(provider.AnalysisError, match="codex_request_failed"):
        asyncio.run(instance.request(socket, 23, "account/read", {}))
    socket = RpcSocket(*[{"method": "unrelated/update", "params": {}} for _ in range(1000)])
    with pytest.raises(provider.AnalysisError, match="too_many_server_events"):
        asyncio.run(instance.request(socket, 24, "account/read", {}))
    assert len(instance.pending) == 1000


def test_receive_rejects_nonobject_json_as_a_protocol_error():
    socket = RpcSocket(["private server response"])
    instance, _ = client(socket)
    with pytest.raises(provider.AnalysisError, match="invalid_server_message"):
        asyncio.run(instance.receive(socket))


@pytest.mark.parametrize("phase,expected_searches", [("started", 1), ("completed", 2)])
def test_web_search_budget_stops_the_next_request_or_records_an_already_completed_excess_search(
    phase, expected_searches
):
    events = [
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "search-1", "type": "webSearch"},
            },
        },
        {
            "method": f"item/{phase}",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "search-2", "type": "webSearch"},
            },
        },
    ]
    socket = WebSocket(extra=events)
    instance, ledger = client(socket)
    instance.search_limit = 1
    with pytest.raises(provider.AnalysisError, match="inference_search_budget_exhausted"):
        instance.complete(
            "Research from primary public sources", {"type": "object"}, allow_web_search=True
        )
    assert instance.web_search_count == expected_searches
    assert ledger[-1]["web_searches"] == expected_searches and ledger[-1]["status"] == "failed"
    assert any(item.get("method") == "turn/interrupt" for item in socket.sent)


def test_capacity_events_use_the_longest_bounded_quota_reset_when_a_turn_fails(monkeypatch):
    monkeypatch.setattr(provider.time, "time", lambda: 1800000000)
    events = [
        {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "primary": {"usedPercent": 100, "resetsAt": 1800000060},
                    "secondary": {"usedPercent": 100, "resetsAt": 1800090000},
                }
            },
        },
        {
            "method": "error",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "error": {"codexErrorInfo": "UsageLimitExceeded"},
            },
        },
    ]
    socket = WebSocket(extra=events, status="failed")
    instance, ledger = client(socket)
    with pytest.raises(provider.AnalysisError, match="codex_usage_limit") as caught:
        instance.complete("Analyze supplied evidence", {"type": "object"})
    assert caught.value.retry_after == 86400 and instance.retry_after == 86400
    assert ledger[-1]["status"] == "failed"


def test_server_without_connectors_can_complete_with_the_same_restricted_permissions():
    class NoConnectors(WebSocket):
        async def send(self, raw):
            await super().send(raw)
            if json.loads(raw).get("method") == "config/read":
                self.messages[-1]["result"]["config"]["mcp_servers"] = {}

    socket = NoConnectors()
    instance, ledger = client(socket)

    assert instance.complete("Analyze supplied text", {"type": "object"}) == {"answer": "ok"}

    thread = next(frame["params"] for frame in socket.sent if frame.get("method") == "thread/start")
    assert thread["config"].get("mcp_servers", {}) == {}
    assert thread["approvalPolicy"] == "never"
    assert thread["config"]["permissions"][thread["permissions"]] == {
        "filesystem": {"/": "deny"},
        "network": {"enabled": False},
    }
    assert ledger[-1]["status"] == "returned"


def test_malformed_quota_windows_do_not_invent_a_pause_or_discard_a_completed_turn():
    socket = WebSocket(
        extra=[
            {
                "method": "account/rateLimits/updated",
                "params": {
                    "rateLimits": {
                        "primary": {"usedPercent": "100", "resetsAt": 1_900_000_000},
                        "secondary": {"usedPercent": 100, "resetsAt": "unknown"},
                    }
                },
            }
        ]
    )
    instance, ledger = client(socket)

    assert instance.complete("Analyze supplied text", {"type": "object"}) == {"answer": "ok"}
    assert instance.retry_after == 0 and ledger[-1]["status"] == "returned"


def test_unknown_rpc_error_object_is_sanitized_without_misclassifying_provider_capacity():
    socket = RpcSocket(
        {"id": 23, "error": {"code": -32603, "message": "private credentials", "data": {}}}
    )
    instance, ledger = client(socket)

    with pytest.raises(provider.AnalysisError) as failure:
        asyncio.run(instance.request(socket, 23, "account/read", {}))

    assert str(failure.value) == "codex_request_failed"
    assert instance.retry_after == 0 and ledger == []


def test_provider_tool_request_during_an_rpc_is_denied_without_continuing_the_rpc():
    socket = RpcSocket(
        {"id": "provider-tool", "method": "item/tool/call", "params": {"command": "read secret"}},
        {"id": 23, "result": {"account": None}},
    )
    instance, ledger = client(socket)

    with pytest.raises(provider.AnalysisError, match="unexpected_server_request"):
        asyncio.run(instance.request(socket, 23, "account/read", {}))

    assert socket.sent[-1] == {
        "id": "provider-tool",
        "error": {"code": -32601, "message": "Interactive and tool requests are not supported"},
    }
    assert len(socket.messages) == 1 and ledger == []


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            ConnectionClosedError(Close(1009, "private peer detail"), None),
            "oversized_server_message",
        ),
        (
            ConnectionClosedError(Close(1011, "private peer detail"), None),
            "codex_connection_closed",
        ),
        (InvalidHandshake("private handshake detail"), "codex_handshake_failed"),
    ],
)
def test_websocket_close_and_handshake_failures_have_safe_distinct_receipts(error, reason):
    ledger = []
    connector = Mock(side_effect=error)
    instance = provider.CodexClient(settings(), connector=connector, usage_recorder=ledger.append)

    with pytest.raises(provider.AnalysisError) as failure:
        instance.complete("Analyze supplied text", {"type": "object"})

    assert str(failure.value) == reason and "private" not in str(failure.value)
    assert ledger[-1]["status"] == "failed" and ledger[-1]["tokens"] == {}


def test_many_individually_valid_frames_cannot_exceed_the_total_response_budget():
    socket = WebSocket(
        extra=[{"method": "unrelated/update", "params": {"text": "x" * 500_000}}] * 5
    )
    instance, ledger = client(socket)

    with pytest.raises(provider.AnalysisError, match="server_event_budget_exceeded"):
        instance.complete("Analyze supplied text", {"type": "object"})

    assert ledger[-1]["status"] == "failed"
    assert any(frame.get("method") == "turn/interrupt" for frame in socket.sent)
    assert any(frame.get("method") == "thread/unsubscribe" for frame in socket.sent)


def test_tiny_notification_flood_is_bounded_without_accepting_a_later_final_answer():
    socket = WebSocket(extra=[{"method": "unrelated/update", "params": {}}] * 10_000)
    instance, ledger = client(socket)

    with pytest.raises(provider.AnalysisError, match="too_many_server_events"):
        instance.complete("Analyze supplied text", {"type": "object"})

    assert ledger[-1]["status"] == "failed"
    assert any(frame.get("method") == "turn/interrupt" for frame in socket.sent)


def test_structured_usage_limit_error_remains_a_capacity_failure_in_the_final_turn_receipt():
    socket = WebSocket(
        status="failed",
        extra=[
            {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "error": {"codexErrorInfo": {"UsageLimitExceeded": {}}},
                },
            }
        ],
    )
    instance, ledger = client(socket)

    with pytest.raises(provider.AnalysisError, match="codex_usage_limit"):
        instance.complete("Analyze supplied text", {"type": "object"})

    assert ledger[-1]["status"] == "failed"
