import asyncio
import json
from collections import deque

import pytest
from devfeed_aggregator.codex_client import AnalysisError, CodexClient
from devfeed_core.config import Settings
from pydantic import ValidationError


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql://db.invalid/test",
            "redis_url": "redis://redis.invalid/15",
            "ai_enabled": True,
            "codex_app_server_url": "ws://127.0.0.1:4500",
            "codex_model": "operator-selected-model",
            **overrides,
        },
    )


class WebSocket:
    def __init__(self, *, early=False, output='{"answer":"ok"}', extra=None, status="completed"):
        self.messages = deque()
        self.sent = []
        self.early, self.output, self.extra, self.status = early, output, extra or [], status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        method = message.get("method", "")
        if method == "initialize":
            result = {}
        elif method == "config/read":
            result = {"config": {"mcp_servers": {"private": {"enabled": True}}}}
        elif method == "thread/start":
            result = {"thread": {"id": "thread-1"}}
        elif method == "turn/start":
            result = {"turn": {"id": "turn-1"}}
        else:
            return
        reply = {"id": message["id"], "result": result}
        if method != "turn/start":
            self.messages.append(reply)
            return
        events = [
            *self.extra,
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "delta": "Not the final JSON",
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"type": "agentMessage", "phase": "final_answer", "text": self.output},
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": self.status}},
            },
        ]
        self.messages.extend([*events, reply] if self.early else [reply, *events])

    async def recv(self):
        return json.dumps(self.messages.popleft())


@pytest.mark.parametrize("early", [True, False])
def test_schema_final_output_and_early_notifications(early):
    ws = WebSocket(early=early)
    client = CodexClient(settings(), connector=lambda *a, **kw: ws)
    assert client.complete("Analyze this", {"type": "object"}) == {"answer": "ok"}
    start = next(item["params"] for item in ws.sent if item.get("method") == "thread/start")
    assert start["ephemeral"] is True and start["config"]["web_search"] == "disabled"
    assert start["config"]['mcp_servers."private".enabled'] is False
    assert start["config"]["features"]["shell_tool"] is False
    assert start["config"]["features"]["hooks"] is False
    turn = next(item["params"] for item in ws.sent if item.get("method") == "turn/start")
    assert turn["outputSchema"] == {"type": "object"}
    assert turn["model"] == "operator-selected-model"
    assert turn["sandboxPolicy"]["networkAccess"] is False
    assert turn["sandboxPolicy"]["access"] == {
        "type": "restricted",
        "includePlatformDefaults": False,
        "readableRoots": [],
    }
    assert any(item.get("method") == "thread/unsubscribe" for item in ws.sent)


@pytest.mark.parametrize("output", ["not json", "[]", "null", "x" * 65_000])
def test_invalid_or_oversized_output_never_becomes_success(output):
    with pytest.raises(AnalysisError):
        CodexClient(settings(), connector=lambda *a, **kw: WebSocket(output=output)).complete(
            "input", {}
        )


def test_failed_turn_does_not_accept_valid_looking_final_json():
    with pytest.raises(AnalysisError, match="codex_turn_failed"):
        CodexClient(settings(), connector=lambda *a, **kw: WebSocket(status="failed")).complete(
            "input", {}
        )


@pytest.mark.parametrize("early", [True, False])
def test_tool_activity_is_not_accepted_as_analysis(early):
    event = {
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "commandExecution"},
        },
    }
    ws = WebSocket(early=early, extra=[event])
    with pytest.raises(AnalysisError, match="unexpected_tool"):
        CodexClient(settings(), connector=lambda *a, **kw: ws).complete("input", {})


def test_interactive_requests_are_refused_and_authentication_not_in_errors():
    event = {
        "id": 100,
        "method": "item/commandExecution/requestApproval",
        "params": {"secret": "sensitive"},
    }
    ws = WebSocket(extra=[event])
    with pytest.raises(AnalysisError) as caught:
        CodexClient(settings(), connector=lambda *a, **kw: ws).complete("input", {})
    assert str(caught.value) == "unexpected_server_request"
    assert any(item.get("id") == 100 and "error" in item for item in ws.sent)


@pytest.mark.parametrize(
    "values",
    [
        {"codex_model": None},
        {"codex_app_server_url": None},
        {"codex_app_server_url": "ws://private.example:4500"},
        {"codex_app_server_url": "wss://user:secret@example.com"},
        {"codex_app_server_url": "wss://example.com"},
    ],
)
def test_explicit_ai_configuration_and_secure_remote_transport(values):
    with pytest.raises(ValidationError):
        settings(**values)


def test_client_does_not_connect_when_disabled_or_input_is_oversized():
    def no_connection(*a, **kw):
        pytest.fail("Unexpected network connection")

    with pytest.raises(AnalysisError, match="ai_not_configured"):
        CodexClient(settings(ai_enabled=False), connector=no_connection).complete("input", {})
    with pytest.raises(AnalysisError, match="input_too_large"):
        CodexClient(settings(), connector=no_connection).complete("a" * 250_001, {})


def test_transport_errors_are_sanitized():
    def connection(*a, **kw):
        raise RuntimeError("ws://secret:password@host.invalid")

    with pytest.raises(AnalysisError) as caught:
        asyncio.run(CodexClient(settings(), connector=connection).complete_async("input", {}))
    assert "secret" not in str(caught.value) and "password" not in str(caught.value)
