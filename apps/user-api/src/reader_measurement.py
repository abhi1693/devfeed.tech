"""Reader-only capture and authenticated diagnostic linkage; best effort."""

import json
import logging
from urllib.parse import unquote

from devfeed_core.reader_acquisition import sanitize_context, unavailable_context

logger = logging.getLogger(__name__)
HEADER = "x-devfeed-reader-measurement"
MAX_CONTEXT_BYTES = 1536


def permitted_context(settings, value):
    try:
        context = sanitize_context(value, campaigns=settings.reader_acquisition_campaigns)
        platform = context.get("client_platform")
        enabled = (
            settings.analytics_enabled
            if platform == "web"
            else settings.extension_analytics_enabled
        )
        if not settings.reader_measurement_enabled or not enabled:
            return unavailable_context(disabled=True)
        return context
    except Exception:
        return unavailable_context()


def capture_context(request, settings, *, cookie=False):
    """Untrusted capture must not raise into authentication or article opens."""
    try:
        if request.headers.get("dnt") == "1" or request.headers.get("sec-gpc") == "1":
            return unavailable_context(disabled=True)
        raw = (
            request.cookies.get(
                "__Host-devfeed_user_acquisition"
                if settings.cookie_secure
                else "devfeed_user_acquisition",
                "",
            )
            if cookie
            else request.headers.get(HEADER, "")
        )
        if not raw or len(raw.encode("utf-8")) > MAX_CONTEXT_BYTES:
            return unavailable_context()
        decoded = unquote(raw) if cookie else raw
        if len(decoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
            return unavailable_context()
        return permitted_context(settings, json.loads(decoded))
    except Exception:
        return unavailable_context()


def bind_measurement(context):
    if context.get("measurement") != "eligible":
        return {}
    return {"client_id": context["client_id"], "client_platform": context["client_platform"]}


def callback_context(request, settings, frozen):
    context = permitted_context(settings, frozen)
    current = capture_context(request, settings, cookie=True)
    if current.get("status") == "unavailable":
        return current
    if context.get("measurement") == "eligible" and (
        current.get("measurement") != "eligible"
        or current.get("client_id") != context.get("client_id")
        or current.get("client_platform") != context.get("client_platform")
    ):
        context = {
            key: value for key, value in context.items() if key not in {"client_id", "session_id"}
        }
        context["measurement"] = "unavailable"
    return context


def session_binding(request, settings, user_id):
    """Optional linkage failures cannot suppress the first-party reading outcome."""
    try:
        from devfeed_user_api import oidc
        from devfeed_user_api.auth import TOKEN, key
        from devfeed_user_api.dependencies import get_redis

        token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
        raw = get_redis().get(key("session", token)) if TOKEN.fullmatch(token) else None
        record = json.loads(raw) if raw else {}
        if record.get("user_id") != str(user_id):
            return {}
        binding = record.get("reader_measurement", {})
        return binding if isinstance(binding, dict) else {}
    except Exception:
        return {}
