"""Monitor the private app-server and let Codex own the entire account login.

Only allowlisted account fields leave this module. No tokens, server errors,
prompts, or arbitrary RPC methods are exposed by the administration API.
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from devfeed_core.config import Settings
from devfeed_core.version import __version__
from pydantic import BaseModel
from websockets.asyncio.client import ClientConnection, connect, unix_connect

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 10
RPC_TIMEOUT = 5
LOGIN_TIMEOUT = 15 * 60
DEVICE_URL = "https://auth.openai.com/codex/device"


class DeviceLogin(BaseModel):
    login_id: str
    status: Literal["pending", "completed", "failed", "cancelled", "expired"] = "pending"
    user_code: str | None = None
    verification_url: str | None = None
    expires_at: datetime
    message: str | None = None


class CodexStatus(BaseModel):
    state: Literal[
        "disabled", "checking", "unavailable", "signed_out", "connected", "limited", "error"
    ]
    message: str
    model: str | None = None
    email: str | None = None
    plan: str | None = None
    checked_at: datetime | None = None
    login: DeviceLogin | None = None


class ConnectionProblem(Exception):
    """A safe message suitable for the admin UI."""


class RpcProblem(Exception):
    def __init__(self, *, unauthorized: bool = False):
        self.unauthorized = unauthorized


class CodexConnection:
    def __init__(self, settings: Settings, *, connector=None):
        self.settings = settings
        self.connector = connector
        self.ws: ClientConnection | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.request_id = 0
        self.events: deque[dict] = deque(maxlen=8)
        self.last_provider_check = 0.0
        self.provider_state: Literal["connected", "limited", "error", "signed_out"] = "connected"
        self.status = CodexStatus(
            state="checking" if settings.ai_enabled else "disabled",
            message="Checking Codex…" if settings.ai_enabled else "AI analysis is disabled.",
            model=settings.codex_model,
        )

    def start(self):
        if self.settings.ai_enabled and self.task is None:
            self.task = asyncio.create_task(self._monitor())

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        async with self.lock:
            # A stopped admin process must not leave a live, unobservable login.
            if self.ws and self.status.login and self.status.login.status == "pending":
                with suppress(Exception):
                    await self._rpc("account/login/cancel", {"loginId": self.status.login.login_id})
            await self._disconnect()

    async def _disconnect(self):
        ws, self.ws = self.ws, None
        if ws:
            with suppress(Exception):
                await ws.close()

    def _finish_login(self, status, message=None):
        if self.status.login:
            self.status.login = self.status.login.model_copy(
                update={
                    "status": status,
                    "message": message,
                    "user_code": None,
                    "verification_url": None,
                }
            )

    def _unavailable(self):
        if self.status.login and self.status.login.status == "pending":
            self._finish_login("failed", "Connection interrupted. Start a new sign-in attempt.")
        self._set_status("unavailable", "Codex is not responding. Check or restart the AI service.")
        self.status.email = self.status.plan = None

    def _set_status(self, state, message):
        if state != self.status.state:
            logger.info("codex_connection_changed", extra={"connection_state": state})
        self.status.state = state
        self.status.message = message
        self.status.checked_at = datetime.now(UTC)

    async def _connect(self):
        if self.ws is not None:
            return
        if not self.settings.ai_enabled:
            raise ConnectionProblem("Enable AI analysis before connecting an account.")
        endpoint = self.settings.codex_app_server_url or ""
        local = urlsplit(endpoint).scheme == "unix"
        connector = self.connector or (unix_connect if local else connect)
        headers = (
            {"Authorization": "Bearer " + self.settings.codex_auth_token.get_secret_value()}
            if self.settings.codex_auth_token
            else {}
        )
        self.ws = await connector(
            urlsplit(endpoint).path if local else endpoint,
            additional_headers=headers,
            max_size=64_000,
            open_timeout=RPC_TIMEOUT,
            close_timeout=1,
            proxy=None,
        )
        self.events.clear()
        self.last_provider_check = 0
        try:
            await self._rpc(
                "initialize", {"clientInfo": {"name": "devfeed-admin", "version": __version__}}
            )
            assert self.ws is not None
            await self.ws.send(json.dumps({"method": "initialized", "params": {}}))
        except Exception:
            await self._disconnect()
            raise

    async def _rpc(self, method, params=None, *, timeout=RPC_TIMEOUT):
        if self.ws is None:
            raise ConnectionError("Codex disconnected")
        self.request_id += 1
        request_id = self.request_id
        async with asyncio.timeout(timeout):
            await self.ws.send(
                json.dumps({"id": request_id, "method": method, "params": params or {}})
            )
            for _ in range(1000):
                message = json.loads(await self.ws.recv())
                if not isinstance(message, dict):
                    raise ValueError("Invalid RPC response")
                if "method" in message:
                    if "id" in message:
                        # This connection can never execute a tool or supply credentials.
                        await self.ws.send(
                            json.dumps(
                                {
                                    "id": message["id"],
                                    "error": {"code": -32601, "message": "Unsupported request"},
                                }
                            )
                        )
                    elif message["method"] == "account/login/completed":
                        self.events.append(message.get("params", {}))
                    elif message["method"] == "account/updated":
                        self.last_provider_check = 0
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    # Inspect only to classify authentication errors; never expose the payload.
                    raw = str(message["error"]).lower()
                    raise RpcProblem(
                        unauthorized=any(
                            value in raw
                            for value in (
                                "401",
                                "unauthorized",
                                "refresh_token",
                                "invalid_grant",
                                "not authenticated",
                            )
                        )
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise ValueError("Invalid RPC result")
                return result
            raise ValueError("Too many RPC messages")

    def _apply_events(self):
        while self.events:
            event = self.events.popleft()
            if not isinstance(event, dict) or not self.status.login:
                continue
            if event.get("loginId") != self.status.login.login_id:
                continue
            if self.status.login.status != "pending":
                continue
            success = event.get("success") is True
            self._finish_login(
                "completed" if success else "failed",
                None
                if success
                else "Sign-in did not complete. Try again and approve the code in ChatGPT.",
            )
            self.last_provider_check = 0

    async def check(self):
        if not self.settings.ai_enabled:
            return
        async with self.lock:
            try:
                await self._connect()
                result = await self._rpc("account/read", {"refreshToken": False})
                self._apply_events()
                login = self.status.login
                if login and login.status == "pending" and login.expires_at <= datetime.now(UTC):
                    await self._rpc("account/login/cancel", {"loginId": login.login_id})
                    self._finish_login("expired", "This code expired. Start a new sign-in attempt.")
                account = result.get("account")
                if not isinstance(result.get("requiresOpenaiAuth"), bool):
                    raise ValueError("Invalid account response")
                self.status.email = self.status.plan = None
                if account is None and result["requiresOpenaiAuth"]:
                    self._set_status(
                        "signed_out", "Connect your ChatGPT account to run AI analysis."
                    )
                    return
                if account is not None and not isinstance(account, dict):
                    raise ValueError("Invalid account")
                if account and account.get("type") == "chatgpt":
                    self.status.email = self._text(account.get("email"))
                    self.status.plan = self._text(account.get("planType"))
                    if time.monotonic() - self.last_provider_check >= 60:
                        self.provider_state = "connected"
                        try:
                            limits = await self._rpc("account/rateLimits/read")
                            windows = limits.get("rateLimits", {})
                            if isinstance(windows, dict) and any(
                                isinstance(windows.get(key), dict)
                                and isinstance(windows[key].get("usedPercent"), (int, float))
                                and windows[key]["usedPercent"] >= 100
                                for key in ("primary", "secondary")
                            ):
                                self.provider_state = "limited"
                        except RpcProblem as exc:
                            self.provider_state = "signed_out" if exc.unauthorized else "error"
                        self.last_provider_check = time.monotonic()
                    state = self.provider_state
                else:
                    state = "connected"
                self._set_status(
                    state,
                    {
                        "connected": "Codex is online and an account is connected."
                        if account
                        else "Codex is online. Its provider does not require a ChatGPT account.",
                        "limited": "Codex is online, but the account has reached its usage limit.",
                        "signed_out": "The account needs reconnecting. Sign in to ChatGPT again.",
                        "error": "Codex is online, but the account service could not be checked. "
                        "Check OpenAI connectivity.",
                    }[state],
                )
            except RpcProblem as exc:
                self._set_status(
                    "signed_out" if exc.unauthorized else "error",
                    "Codex could not verify the account. Try connecting to ChatGPT again.",
                )
                self._apply_events()
            except Exception:
                self._unavailable()
                await self._disconnect()

    @staticmethod
    def _text(value):
        return value[:254] if isinstance(value, str) else None

    async def _monitor(self):
        while True:
            await self.check()
            await asyncio.sleep(CHECK_INTERVAL)

    async def login(self):
        async with self.lock:
            if self.status.login and self.status.login.status == "pending":
                return self.status.login
            try:
                await self._connect()
                result = await self._rpc(
                    "account/login/start", {"type": "chatgptDeviceCode"}, timeout=15
                )
                if (
                    result.get("type") != "chatgptDeviceCode"
                    or result.get("verificationUrl") != DEVICE_URL
                    or not isinstance(result.get("loginId"), str)
                    or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", result["loginId"])
                    or not isinstance(result.get("userCode"), str)
                    or not re.fullmatch(r"[A-Za-z0-9-]{4,32}", result["userCode"])
                ):
                    raise RpcProblem()
                self.status.login = DeviceLogin(
                    login_id=result["loginId"],
                    user_code=result["userCode"],
                    verification_url=DEVICE_URL,
                    expires_at=datetime.now(UTC) + timedelta(seconds=LOGIN_TIMEOUT),
                )
                self._apply_events()
                return self.status.login
            except ConnectionProblem:
                raise
            except RpcProblem:
                raise ConnectionProblem(
                    "Could not start ChatGPT sign-in. Check that device-code login is enabled in "
                    "your ChatGPT security settings and that Codex can reach OpenAI."
                ) from None
            except Exception:
                self._unavailable()
                await self._disconnect()
                raise ConnectionProblem(
                    "Codex is not responding. Restore the AI service and try again."
                ) from None

    async def cancel(self, login_id: str):
        async with self.lock:
            if not self.status.login or self.status.login.login_id != login_id:
                raise ConnectionProblem("This sign-in attempt is no longer current.")
            if self.status.login.status != "pending":
                return
            try:
                await self._rpc("account/login/cancel", {"loginId": login_id})
                self._finish_login("cancelled")
            except Exception:
                self._unavailable()
                await self._disconnect()
                raise ConnectionProblem(
                    "Codex is unavailable. The sign-in attempt was interrupted."
                ) from None
