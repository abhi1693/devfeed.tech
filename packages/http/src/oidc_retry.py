"""Short-lived, single-use recovery after a browser-bound OIDC callback.

Only an already validated flow may mint a context. It contains no code, nonce,
PKCE verifier, provider response or registration intent. Service-specific key and
policy scopes plus the HttpOnly state cookie keep contexts out of other browsers.
"""

import hmac
import json
import secrets
from urllib.parse import urlencode

from devfeed_http import oidc

TTL = 300


class ExpiredRetry(oidc.OIDCError):
    """No usable recovery context remains; start a new sign-in."""


def save(redis, *, key, browser: str, policy: str, destination: object = None) -> str:
    if not oidc.TOKEN.fullmatch(browser):
        raise ExpiredRetry()
    token = secrets.token_urlsafe(32)
    redis.set(
        key("retry", token),
        json.dumps({"browser": browser, "policy": policy, "destination": destination}),
        ex=TTL,
    )
    return token


def consume(redis, *, key, token: str, browser: str, policy: str) -> dict:
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
