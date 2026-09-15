"""Pace the shared account allowance without inventing a tokens-to-quota conversion."""

import json
import math
import time
from datetime import UTC, datetime, timedelta

from devfeed_core.ai_capacity import cooldown_key
from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis

FRESH_SECONDS = 180


def quota_key():
    return cooldown_key() + ":quota"


def weekly_window(response):
    limits = response.get("rateLimits") if isinstance(response, dict) else None
    if not isinstance(limits, dict):
        return None
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict) or window.get("windowDurationMins") != 10080:
            continue
        used, reset = window.get("usedPercent"), window.get("resetsAt")
        if (
            isinstance(used, (int, float))
            and not isinstance(used, bool)
            and isinstance(reset, (int, float))
            and not isinstance(reset, bool)
            and math.isfinite(used)
            and math.isfinite(reset)
            and 0 <= used <= 100
        ):
            return float(used), float(reset)
    return None


def paced_snapshot(previous, response, *, now: float, reserve: float):
    window = weekly_window(response)
    if window is None:
        return None
    used, reset = window
    if not now < reset <= now + 7 * 86400:
        return None
    midnight = datetime.fromtimestamp(now, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = min((midnight + timedelta(days=1)).timestamp(), reset)
    # Allocate this day's remaining fraction of the remaining week. Other clients
    # spend the same account quota and therefore reduce our headroom automatically.
    usable = max(0, 100 - reserve - used)
    ceiling = used + usable * (day_end - now) / (reset - now)
    if (
        isinstance(previous, dict)
        and previous.get("resets_at") == reset
        and previous.get("day_end") == day_end
        and previous.get("used_percent", 101) <= used
        and previous.get("reserve") == reserve
    ):
        ceiling = previous["ceiling_percent"]
    return {
        "used_percent": used,
        "resets_at": reset,
        "day_end": day_end,
        "ceiling_percent": min(100 - reserve, ceiling),
        "reserve": reserve,
        "checked_at": now,
        "provider_allowed": response.get("ordinaryUsageAllowed") is not False,
    }


def pause_reason(connection, *, settings=None, now=None):
    settings = settings or get_settings()
    if not settings.ai_quota_pacing_enabled:
        return None
    now = time.time() if now is None else now
    try:
        raw = connection.get(quota_key())
        snapshot = json.loads(raw) if raw else None
        if not snapshot or not 0 <= now - snapshot["checked_at"] <= FRESH_SECONDS:
            return "codex_quota_unavailable"
        if now >= min(snapshot["day_end"], snapshot["resets_at"]):
            return "codex_quota_unavailable"
        if (
            not snapshot["provider_allowed"]
            or snapshot["used_percent"] >= snapshot["ceiling_percent"]
        ):
            return "codex_quota_paced"
    except (ValueError, TypeError, KeyError):
        return "codex_quota_unavailable"
    return None


def current_pause_reason(settings=None):
    settings = settings or get_settings()
    if not settings.ai_quota_pacing_enabled:
        return None
    try:
        with create_redis(settings) as connection:
            return pause_reason(connection, settings=settings)
    except Exception:
        return "codex_quota_unavailable"
