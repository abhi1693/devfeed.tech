"""The admin Codex monitor accepts bounded account RPC and never executes provider requests."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_admin_api import codex_connection as connection
from test_source_readiness_unit import NOW, configuration


@pytest.fixture(autouse=True)
def fixed_monitor_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(connection, "datetime", Clock)


def monitor(frames=()):
    client = connection.CodexConnection(configuration(ai_enabled=True))
    ws = SimpleNamespace(
        send=AsyncMock(),
        recv=AsyncMock(side_effect=[json.dumps(frame) for frame in frames]),
        close=AsyncMock(),
    )
    client.ws = ws
    return client, ws


def pending(client, **changes):
    client.status.login = connection.DeviceLogin(
        login_id="current-login",
        user_code="ABCD-1234",
        verification_url=connection.DEVICE_URL,
        expires_at=NOW + timedelta(minutes=15),
        **changes,
    )


def test_provider_tool_and_credential_requests_are_refused_without_interrupting_account_reply():
    async def run():
        client, ws = monitor(
            [
                {
                    "id": "provider-request",
                    "method": "item/tool/call",
                    "params": {"command": "read credential"},
                },
                {"id": 999, "result": {"private": "unrelated response"}},
                {"id": 1, "result": {"account": None, "requiresOpenaiAuth": True}},
            ]
        )
        assert await client._rpc("account/read") == {"account": None, "requiresOpenaiAuth": True}
        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        assert sent == [
            {"id": 1, "method": "account/read", "params": {}},
            {"id": "provider-request", "error": {"code": -32601, "message": "Unsupported request"}},
        ]
        assert ws.recv.await_count == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "frame", [[], {"id": 1, "result": None}, {"id": 1, "result": ["private account"]}]
)
def test_rpc_rejects_nonobject_envelopes_and_results(frame):
    async def run():
        client, _ = monitor([frame])
        with pytest.raises(ValueError, match="Invalid RPC"):
            await client._rpc("account/read")

    asyncio.run(run())


def test_notification_flood_is_bounded_even_if_no_matching_account_reply_arrives():
    async def run():
        client, ws = monitor([{"method": "unrelated/event", "params": {}}] * 1000)
        with pytest.raises(ValueError, match="Too many RPC messages"):
            await client._rpc("account/read")
        assert ws.recv.await_count == 1000
        assert ws.send.await_count == 1

    asyncio.run(run())


def test_account_update_notification_invalidates_cached_provider_check_without_becoming_a_reply():
    async def run():
        client, ws = monitor(
            [
                {"method": "account/updated", "params": {"private": "ignored"}},
                {"id": 1, "result": {"account": None, "requiresOpenaiAuth": False}},
            ]
        )
        client.last_provider_check = 45
        assert (await client._rpc("account/read"))["requiresOpenaiAuth"] is False
        assert client.last_provider_check == 0 and ws.recv.await_count == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "event,status",
    [
        ([], "pending"),
        ({"loginId": "old-login", "success": True}, "pending"),
        ({"loginId": "current-login", "success": "true", "error": "private diagnostic"}, "failed"),
        ({"loginId": "current-login", "success": True}, "cancelled"),
    ],
)
def test_login_events_cannot_finish_another_or_terminal_attempt_or_treat_truthy_text_as_success(
    event, status
):
    client, _ = monitor()
    pending(client, status="cancelled" if status == "cancelled" else "pending")
    if status == "cancelled":
        client._finish_login("cancelled")
    client.events.append(event)
    client._apply_events()
    assert client.status.login.status == status
    if status == "pending":
        assert client.status.login.user_code == "ABCD-1234"
    else:
        assert (
            client.status.login.user_code is None and client.status.login.verification_url is None
        )
    assert "private" not in client.status.model_dump_json()
    assert not client.events


def test_login_events_without_an_active_attempt_are_discarded():
    client, _ = monitor()
    client.events.append({"loginId": "expired-login", "success": True})
    client._apply_events()
    assert client.status.login is None and not client.events


@pytest.mark.parametrize("reply", [{"account": None}, {"account": [], "requiresOpenaiAuth": False}])
def test_malformed_account_reply_clears_previous_identity_and_disconnects(
    reply,
):
    async def run():
        client, ws = monitor([{"id": 1, "result": reply}])
        client.status.email, client.status.plan = "previous@example.com", "private plan"
        await client.check()
        assert client.status.state == "unavailable" and client.ws is None
        assert client.status.email is None and client.status.plan is None
        ws.close.assert_awaited_once_with()
        assert "previous@example.com" not in client.status.model_dump_json()

    asyncio.run(run())


@pytest.mark.parametrize("unauthorized", [False, True])
def test_account_rpc_failure_is_classified_without_returning_upstream_diagnostics(unauthorized):
    async def run():
        message = "401 private credential" if unauthorized else "provider private failure"
        client, _ = monitor([{"id": 1, "error": {"message": message}}])
        await client.check()
        assert client.status.state == ("signed_out" if unauthorized else "error")
        assert client.status.email is None and client.status.quota == []
        assert "private" not in client.status.model_dump_json()

    asyncio.run(run())


def test_failed_initialize_closes_half_open_socket_before_next_connection_attempt():
    async def run():
        ws = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(return_value="[]"), close=AsyncMock())
        connector = AsyncMock(return_value=ws)
        client = connection.CodexConnection(configuration(ai_enabled=True), connector=connector)
        with pytest.raises(ValueError, match="Invalid RPC response"):
            await client._connect()
        assert client.ws is None
        ws.close.assert_awaited_once_with()
        assert [json.loads(call.args[0])["method"] for call in ws.send.call_args_list] == [
            "initialize"
        ]

    asyncio.run(run())


@pytest.mark.parametrize("action", ["login", "cancel"])
def test_transport_loss_during_signin_is_safe_and_discards_the_now_unusable_code(action):
    async def run():
        client, ws = monitor()
        if action == "cancel":
            pending(client)
        ws.recv.side_effect = OSError("private endpoint with token")
        with pytest.raises(connection.ConnectionProblem) as failure:
            await (client.login() if action == "login" else client.cancel("current-login"))
        assert "private" not in str(failure.value)
        assert client.status.state == "unavailable" and client.ws is None
        assert (
            client.status.login is None
            if action == "login"
            else client.status.login.status == "failed"
        )
        if client.status.login:
            assert (
                client.status.login.user_code is None
                and client.status.login.verification_url is None
            )
        ws.close.assert_awaited_once_with()

    asyncio.run(run())


def test_repeated_cancel_of_terminal_attempt_does_not_emit_another_provider_command():
    async def run():
        client, ws = monitor()
        pending(client)
        client._finish_login("cancelled")
        await client.cancel("current-login")
        ws.send.assert_not_awaited()
        assert client.status.login.status == "cancelled"

    asyncio.run(run())


def test_missing_transport_rpc_fails_immediately_without_implicitly_connecting():
    async def run():
        connector = Mock()
        client = connection.CodexConnection(configuration(ai_enabled=True), connector=connector)
        with pytest.raises(ConnectionError, match="Codex disconnected"):
            await client._rpc("account/read")
        connector.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("windows", [None, [], "private diagnostic"])
def test_nonobject_quota_payload_is_unknown_not_a_zero_percent_window(windows):
    assert connection.quota_windows(windows) == []
