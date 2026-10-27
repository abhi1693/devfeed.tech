"""Pace the shared account allowance without inventing a tokens-to-quota conversion."""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from devfeed_core.ai_capacity import cooldown_key
from devfeed_core.config import Settings, get_settings
from devfeed_core.json_types import JsonRecord
from devfeed_core.redis import create_redis
from redis import Redis


class QuotaSnapshot(TypedDict):
    used_percent: float
    resets_at: float
    day_end: float
    ceiling_percent: float
    reserve: float
    checked_at: float
    provider_allowed: bool


FRESH_SECONDS = 180


def quota_key() -> str:
    return cooldown_key() + ":quota"


def weekly_window(response: object) -> tuple[float, float] | None:
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


def paced_snapshot(
    previous: JsonRecord | None,
    response: JsonRecord,
    *,
    now: float,
    reserve: float,
) -> QuotaSnapshot | None:
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


def pause_reason(
    connection: Redis,
    *,
    settings: Settings | None = None,
    now: float | None = None,
) -> str | None:
    settings = settings or get_settings()
    if not settings.ai_quota_pacing_enabled:
        return None
    return snapshot_pause_reason(connection.get(quota_key()), settings=settings, now=now)


def snapshot_pause_reason(
    raw: bytes | str | None,
    *,
    settings: Settings,
    now: float | None = None,
) -> str | None:
    """Validate the same allowance when a caller batches its Redis reads."""
    if not settings.ai_quota_pacing_enabled:
        return None
    now = time.time() if now is None else now
    try:
        snapshot: JsonRecord | None = json.loads(raw) if raw else None
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


def current_pause_reason(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if not settings.ai_quota_pacing_enabled:
        return None
    try:
        with create_redis(settings) as connection:
            return pause_reason(connection, settings=settings)
    except Exception:
        return "codex_quota_unavailable"
