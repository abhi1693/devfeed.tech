"""Provider protocol failures stay bounded, content-free and incapable of tool execution."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from devfeed_aggregator import codex_client as provider
from devfeed_core import quota_pacing
from test_codex_client import WebSocket, settings
from test_runtime_provider_protocol_unit import RpcSocket, client
from websockets.exceptions import ConnectionClosedError, InvalidHandshake
from websockets.frames import Close


@pytest.fixture(autouse=True)
def no_external_quota_service(monkeypatch):
    monkeypatch.setattr(quota_pacing, "current_pause_reason", lambda _: None)


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
