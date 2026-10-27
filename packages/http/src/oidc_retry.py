"""Short-lived, single-use recovery after a browser-bound OIDC callback.

Only an already validated flow may mint a context. It contains no code, nonce,
PKCE verifier, provider response or registration intent. Service-specific key and
policy scopes plus the HttpOnly state cookie keep contexts out of other browsers.
"""

import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from urllib.parse import urlencode

from devfeed_core.json_types import JsonRecord
from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from redis import Redis
from redis.exceptions import RedisError

from devfeed_http import oidc

TTL = 300


class ExpiredRetry(oidc.OIDCError):
    """No usable recovery context remains; start a new sign-in."""


def save(
    redis: Redis,
    *,
    key: Callable[[str, str], str],
    browser: str,
    policy: str,
    destination: object = None,
) -> str:
    if not oidc.TOKEN.fullmatch(browser):
        raise ExpiredRetry()
    token = secrets.token_urlsafe(32)
    redis.set(
        key("retry", token),
        json.dumps({"browser": browser, "policy": policy, "destination": destination}),
        ex=TTL,
    )
    return token


def consume(
    redis: Redis, *, key: Callable[[str, str], str], token: str, browser: str, policy: str
) -> JsonRecord:
    if not oidc.TOKEN.fullmatch(token) or not oidc.TOKEN.fullmatch(browser):
        raise ExpiredRetry()
    retry_key = key("retry", token)
    raw = redis.get(retry_key)
    try:
        context = json.loads(raw) if raw else None
        if (
            not isinstance(context, dict)
            or not isinstance(context.get("browser"), str)
            or not hmac.compare_digest(context["browser"], browser)
            or context.get("policy") != policy
        ):
            raise ExpiredRetry()
    except (ValueError, TypeError) as exc:
        raise ExpiredRetry() from exc
    if redis.getdel(retry_key) != raw:
        raise ExpiredRetry()
    return context


def failure_url(base: str, *, reason: str, reference: str, retry: str | None = None) -> str:
    params = {"ref": reference}
    if retry:
        params["retry"] = retry
    # Keep the established error query contract; every value here is application-owned.
    params["error"] = reason
    return base.rstrip("/") + "/login?" + urlencode(params)


@dataclass(frozen=True)
class Recovery:
    """Shared failure plumbing; callers retain flow, identity and destination checks."""

    base_url: str
    browser: str
    policy: str
    key: Callable[[str, str], str]
    cookie: Callable[[Response, str, str, int], None]
    redis: Callable[[], Redis]

    def consume(self, request: Request, token: str | None) -> JsonRecord | None:
        if token is None:
            return None
        if len(request.query_params.getlist("retry")) != 1:
            raise ExpiredRetry()
        return consume(
            self.redis(), key=self.key, token=token, browser=self.browser, policy=self.policy
        )

    def response(
        self, reason: str, *, reference: str | None = None, retry: str | None = None
    ) -> RedirectResponse:
        return RedirectResponse(
            failure_url(
                self.base_url,
                reason=reason,
                reference=reference or secrets.token_urlsafe(12),
                retry=retry,
            ),
            302,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    def _save(self, redis: Redis, destination: object) -> str | None:
        try:
            return save(
                redis,
                key=self.key,
                browser=self.browser,
                policy=self.policy,
                destination=destination,
            )
        except RedisError:
            return None

    def unavailable(self, *, destination: object = None) -> RedirectResponse:
        try:
            token = self._save(self.redis(), destination)
        except RedisError:
            token = None
        response = self.response("login_unavailable", retry=token)
        if token:
            self.cookie(response, "state", self.browser, TTL)
        return response

    def failed_callback(
        self,
        error: Exception,
        *,
        redis: Redis | None,
        logger: Logger,
        event: str,
        bound_flow: bool,
        reason: str = "login_failed",
        destination: object = None,
        retry_allowed: bool = True,
        dependency_errors: tuple[type[Exception], ...] = (),
    ) -> RedirectResponse:
        if isinstance(error, oidc.OIDCCancelled):
            reason = "login_cancelled"
        elif isinstance(error, (oidc.OIDCDependencyError, RedisError, *dependency_errors)):
            reason = "login_unavailable"
        elif isinstance(error, oidc.OIDCFlowUnavailable):
            reason = "login_expired"
        reference = secrets.token_urlsafe(12)
        logger.warning(
            event,
            extra={"error_type": type(error).__name__, "reason": reason, "reference": reference},
        )
        token = (
            self._save(redis, destination)
            if bound_flow and retry_allowed and redis is not None
            else None
        )
        response = self.response(reason, reference=reference, retry=token)
        if bound_flow:
            self.cookie(response, "session", "", 0)
        if token:
            self.cookie(response, "state", self.browser, TTL)
        elif bound_flow:
            self.cookie(response, "state", "", 0)
        return response
