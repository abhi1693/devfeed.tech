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
            result = {
                "thread": {"id": "thread-1"},
                "activePermissionProfile": {
                    "id": message["params"]["permissions"],
                    "extends": None,
                },
            }
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
    assert start["config"]["features"]["code_mode_host"] is False
    turn = next(item["params"] for item in ws.sent if item.get("method") == "turn/start")
    assert turn["outputSchema"] == {"type": "object"}
    assert start["model"] == "operator-selected-model"
    assert start["approvalPolicy"] == "never"
    assert start["config"]["project_doc_max_bytes"] == 0
    assert start["config"]["permissions"][start["permissions"]] == {
        "filesystem": {"/": "deny"},
        "network": {"enabled": False},
    }
    assert "sandbox" not in start and "sandboxPolicy" not in turn
    assert "permissions" not in turn and "model" not in turn
    assert any(item.get("method") == "thread/unsubscribe" for item in ws.sent)


@pytest.mark.parametrize(
    "active", [None, {}, {"id": "wrong"}, {"id": "same", "extends": ":workspace"}]
)
def test_rejects_unconfirmed_or_inherited_permissions_before_starting_turn(active):
    class Unconfirmed(WebSocket):
        async def send(self, raw):
            await super().send(raw)
            message = json.loads(raw)
            if message.get("method") == "thread/start":
                response = self.messages[-1]
                value = dict(active) if isinstance(active, dict) else active
                if value and value.get("id") == "same":
                    value["id"] = message["params"]["permissions"]
                response["result"]["activePermissionProfile"] = value

    ws = Unconfirmed()
    with pytest.raises(AnalysisError, match="permissions_not_confirmed"):
        CodexClient(settings(), connector=lambda *a, **kw: ws).complete("Analyze", {})
    assert not any(message.get("method") == "turn/start" for message in ws.sent)


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


def test_unix_socket_connects_to_private_bridge_without_network_proxy(monkeypatch):
    from devfeed_aggregator import codex_client

    seen = []

    def connector(path, **kwargs):
        seen.append((path, kwargs))
        return WebSocket()

    monkeypatch.setattr(codex_client, "unix_connect", connector)
    result = CodexClient(
        settings(codex_app_server_url="unix:///run/codex/app-server.sock")
    ).complete("Analyze", {})
    assert result == {"answer": "ok"}
    assert seen[0][0] == "/run/codex/app-server.sock"
    assert seen[0][1]["proxy"] is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "unix://remote/path",
        "unix:///",
        "unix:relative.sock",
        "unix:///tmp/a?token=x",
        "unix:///tmp/a#fragment",
        "unix:///tmp/%2fsocket",
    ],
)
def test_unix_transport_requires_an_unambiguous_local_path(endpoint):
    with pytest.raises(ValidationError):
        settings(codex_app_server_url=endpoint)


@pytest.mark.parametrize("allowed", [False, True])
def test_web_research_requires_explicit_opt_in(allowed):
    event = {
        "method": "item/completed",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "webSearch", "query": "official project"},
        },
    }
    ws = WebSocket(extra=[event])
    client = CodexClient(settings(), connector=lambda *a, **kw: ws)
    if allowed:
        assert client.complete("Research", {}, allow_web_search=True) == {"answer": "ok"}
        start = next(item["params"] for item in ws.sent if item.get("method") == "thread/start")
        assert start["config"]["web_search"] == "live"
        assert client.web_search_count == 1
        assert start["config"]["features"]["code_mode_host"] is True
        assert start["config"]["features"]["shell_tool"] is False
        assert start["config"]['mcp_servers."private".enabled'] is False
        assert start["config"]["permissions"][start["permissions"]]["filesystem"] == {"/": "deny"}
    else:
        with pytest.raises(AnalysisError, match="unexpected_tool"):
            client.complete("Research", {})


@pytest.mark.parametrize(
    "kind", ["commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"]
)
def test_web_research_still_refuses_other_tools(kind):
    event = {
        "method": "item/started",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"type": kind}},
    }
    ws = WebSocket(extra=[event])
    with pytest.raises(AnalysisError, match="unexpected_tool"):
        CodexClient(settings(), connector=lambda *a, **kw: ws).complete(
            "Research", {}, allow_web_search=True
        )
