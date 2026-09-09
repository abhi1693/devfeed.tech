"""Account monitoring against a real local WebSocket server, without OpenAI calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api import codex_connection as module
from devfeed_admin_api.codex_connection import CodexConnection, ConnectionProblem
from devfeed_core.config import Settings
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


class Server:
    def __init__(self):
        self.account = None
        self.requires_auth = True
        self.calls = []
        self.connections = []
        self.error_method = None
        self.error = "internal private-secret"
        self.stall = None
        self.used = 0
        self.early = None
        self.login_result = {
            "type": "chatgptDeviceCode",
            "loginId": "login-1",
            "userCode": "ABCD-1234",
            "verificationUrl": module.DEVICE_URL,
        }

    async def handler(self, ws):
        self.connections.append(ws)
        try:
            async for raw in ws:
                message = json.loads(raw)
                self.calls.append(message)
                method = message.get("method")
                if "id" not in message or method is None:
                    continue
                if method == self.stall:
                    continue
                if method == self.error_method:
                    await ws.send(
                        json.dumps(
                            {"id": message["id"], "error": {"code": -32000, "message": self.error}}
                        )
                    )
                    continue
                result = {}
                if method == "account/read":
                    result = {"account": self.account, "requiresOpenaiAuth": self.requires_auth}
                elif method == "account/rateLimits/read":
                    result = {"rateLimits": {"primary": {"usedPercent": self.used}}}
                elif method == "account/login/start":
                    result = self.login_result
                    if self.early is not None:
                        await ws.send(
                            json.dumps(
                                {
                                    "method": "account/login/completed",
                                    "params": {
                                        "loginId": "login-1",
                                        "success": self.early,
                                        "error": "private-secret",
                                    },
                                }
                            )
                        )
                await ws.send(json.dumps({"id": message["id"], "result": result}))
        except ConnectionClosed:
            pass

    def connected(self):
        self.account = {
            "type": "chatgpt",
            "email": "test@example.com",
            "planType": "plus",
            "accessToken": "private-secret",
        }


async def exercise(callback):
    server = Server()
    async with serve(server.handler, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        settings = Settings(
            _env_file=None,
            database_url="postgresql://db.invalid/unit_test",
            redis_url="redis://redis.invalid/15",
            ai_enabled=True,
            codex_model="test-model",
            codex_app_server_url=f"ws://127.0.0.1:{port}",
        )
        client = CodexConnection(settings)
        try:
            await callback(server, client)
        finally:
            await client.close()


def test_reads_account_without_inference_and_recovers_after_disconnect():
    async def run(server, client):
        await client.check()
        assert client.status.state == "signed_out"
        server.connected()
        await client.check()
        assert client.status.state == "connected"
        assert client.status.email == "test@example.com"
        assert "private-secret" not in client.status.model_dump_json()
        await server.connections[-1].close()
        await client.check()
        assert client.status.state == "unavailable" and client.status.email is None
        await client.check()
        assert client.status.state == "connected"
        assert all(
            not item.get("method", "").startswith(("thread/", "turn/")) for item in server.calls
        )

    asyncio.run(exercise(run))


@pytest.mark.parametrize(
    "failure,state", [("401 private-secret", "signed_out"), ("private-secret", "error")]
)
def test_provider_failure_is_safe_and_recovers(failure, state):
    async def run(server, client):
        server.connected()
        server.error_method = "account/rateLimits/read"
        server.error = failure
        await client.check()
        assert client.status.state == state
        assert "private-secret" not in client.status.model_dump_json()
        server.error_method = None
        client.last_provider_check = 0
        await client.check()
        assert client.status.state == "connected"

    asyncio.run(exercise(run))


def test_usage_limit_is_not_reported_as_a_server_outage():
    async def run(server, client):
        server.connected()
        server.used = 100
        await client.check()
        assert client.status.state == "limited"
        assert "usage limit" in client.status.message

    asyncio.run(exercise(run))


def test_device_login_is_idempotent_and_completion_clears_code():
    async def run(server, client):
        await client.check()
        first, second = await asyncio.gather(client.login(), client.login())
        assert first == second
        assert sum(x.get("method") == "account/login/start" for x in server.calls) == 1
        assert first.verification_url == module.DEVICE_URL
        server.connected()
        await server.connections[-1].send(
            json.dumps(
                {
                    "method": "account/login/completed",
                    "params": {"loginId": first.login_id, "success": True},
                }
            )
        )
        await client.check()
        assert client.status.login.status == "completed"
        assert client.status.login.user_code is None
        assert client.status.state == "connected"

    asyncio.run(exercise(run))


@pytest.mark.parametrize("success", [True, False])
def test_login_completion_before_rpc_reply_is_not_lost(success):
    async def run(server, client):
        server.early = success
        result = await client.login()
        assert result.status == ("completed" if success else "failed")
        assert result.user_code is None
        assert "private-secret" not in result.model_dump_json()

    asyncio.run(exercise(run))


@pytest.mark.parametrize("action", ["cancel", "expire", "disconnect"])
def test_interrupted_login_does_not_leave_a_stale_device_code(action):
    async def run(server, client):
        login = await client.login()
        if action == "cancel":
            with pytest.raises(ConnectionProblem, match="no longer current"):
                await client.cancel("different-login")
            await client.cancel(login.login_id)
        elif action == "expire":
            client.status.login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await client.check()
        else:
            await server.connections[-1].close()
            await client.check()
        assert (
            client.status.login.status
            == {"cancel": "cancelled", "expire": "expired", "disconnect": "failed"}[action]
        )
        assert client.status.login.user_code is None
        assert client.status.login.verification_url is None

    asyncio.run(exercise(run))


@pytest.mark.parametrize(
    "change",
    [
        {"verificationUrl": "https://evil.example"},
        {"userCode": "<script>"},
        {"loginId": "../../other"},
        {"type": "chatgptAuthTokens"},
    ],
)
def test_rejects_unexpected_login_payloads(change):
    async def run(server, client):
        server.login_result.update(change)
        with pytest.raises(ConnectionProblem, match="Could not start"):
            await client.login()
        assert client.status.login is None

    asyncio.run(exercise(run))


def test_unresponsive_server_is_bounded_and_reconnects(monkeypatch):
    # Shorten the RPC deadline without changing its production timeout.
    original = CodexConnection._rpc

    async def fast(self, method, params=None, **kwargs):
        return await original(self, method, params, timeout=0.03)

    monkeypatch.setattr(CodexConnection, "_rpc", fast)

    async def run(server, client):
        server.stall = "account/read"
        await asyncio.wait_for(client.check(), 1)
        assert client.status.state == "unavailable"
        server.stall = None
        await client.check()
        assert client.status.state == "signed_out"

    asyncio.run(exercise(run))


def test_disabled_ai_does_not_connect():
    async def run(server, client):
        client.settings.ai_enabled = False
        disabled = CodexConnection(client.settings)
        disabled.start()
        await disabled.check()
        assert disabled.task is None and disabled.status.state == "disabled"
        with pytest.raises(ConnectionProblem, match="Enable AI"):
            await disabled.login()
        assert server.calls == []

    asyncio.run(exercise(run))


def test_monitor_shutdown_cancels_sign_in_and_closes_transport(monkeypatch):
    monkeypatch.setattr(module, "CHECK_INTERVAL", 0.01)

    async def run(server, client):
        client.start()
        client.start()
        for _ in range(50):
            if client.status.state == "signed_out":
                break
            await asyncio.sleep(0.01)
        assert client.status.state == "signed_out"
        await client.login()
        await client.close()
        assert client.ws is None and client.task is None
        assert any(x.get("method") == "account/login/cancel" for x in server.calls)

    asyncio.run(exercise(run))
