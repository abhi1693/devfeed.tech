"""Bounded source cadence. Learning is committed only with an owned successful job."""

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from devfeed_core.config import get_settings

logger = logging.getLogger(__name__)
HALF_LIFE = 21600
STALE_SECONDS = 7 * 86400
PROBE_SECONDS = 3600


def decide(
    state: dict[str, Any], *, now: datetime, arrivals: int, minimum: int = 300, maximum: int = 86400
) -> dict[str, Any]:
    """Pure time-aware EWMA: one entry/fetch, 6h half-life, 2x/day quiet growth.

    First/stale windows establish a baseline at an hourly probe. Limit each
    observation's influence to eight arrivals; accelerate at most 2x per window.
    Quiet windows grow by elapsed time, not fetch count. Keep a 15% dead band.
    """
    previous_time = (
        datetime.fromisoformat(state["observed_at"]) if state.get("observed_at") else None
    )
    elapsed = (now - previous_time).total_seconds() if previous_time else 0
    previous = max(minimum, min(maximum, int(state.get("interval", PROBE_SECONDS))))
    if previous_time is None or elapsed > STALE_SECONDS:
        return {
            "observed_at": now.isoformat(),
            "rate": 0.0,
            "quiet_seconds": 0,
            "interval": max(minimum, min(maximum, PROBE_SECONDS)),
            "reason": "baseline" if previous_time is None else "stale_probe",
            "last_activity_at": state.get("last_activity_at"),
        }
    if elapsed <= 0:
        return dict(state)
    rate = float(state.get("rate", 0))
    alpha = -math.expm1(-math.log(2) * elapsed / HALF_LIFE)
    rate = (1 - alpha) * rate + alpha * min(max(arrivals, 0), 8) / elapsed
    quiet = 0 if arrivals else float(state.get("quiet_seconds", 0)) + elapsed
    if arrivals:
        target = 1 / rate if rate > 0 else previous
        interval = max(previous / 2, min(previous, target))
        reason = "new_entries"
    else:
        interval = previous * 2 ** (min(elapsed, 86400) / 86400)
        reason = "quiet"
    interval = max(minimum, min(maximum, round(interval)))
    # Apply hysteresis to acceleration only; quiet growth must compose across
    # short windows rather than getting stuck forever inside the dead band.
    if arrivals and abs(interval - previous) < previous * 0.15:
        interval = previous
    return {
        "observed_at": now.isoformat(),
        "rate": rate,
        "quiet_seconds": quiet,
        "interval": interval,
        "reason": reason,
        "last_activity_at": now.isoformat() if arrivals else state.get("last_activity_at"),
    }


def resolve_interval(source) -> int:
    settings = get_settings()
    if settings.adaptive_source_polling_enabled and source.polling_mode == "adaptive":
        return max(
            settings.adaptive_source_polling_min_seconds,
            min(
                settings.adaptive_source_polling_max_seconds,
                int((source.polling_state or {}).get("interval", PROBE_SECONDS)),
            ),
        )
    return source.poll_interval_seconds


def schedule(source, now: datetime) -> datetime:
    """Stable source jitter (±5%), clipped to bounds; fixed cadence is unchanged."""
    interval = resolve_interval(source)
    adaptive = source.effective_polling_mode == "adaptive"
    source.scheduled_polling_mode = "adaptive" if adaptive else "fixed"
    source.scheduled_interval_seconds = interval
    if adaptive:
        settings = get_settings()
        jitter = (int(source.id) % 1001 / 1000 - 0.5) * 0.1
        interval = max(
            settings.adaptive_source_polling_min_seconds,
            min(settings.adaptive_source_polling_max_seconds, round(interval * (1 + jitter))),
        )
    return now + timedelta(seconds=interval)


def observe(source, now: datetime, arrivals: int, *, automatic: bool) -> None:
    settings = get_settings()
    if not settings.adaptive_source_polling_enabled or source.polling_mode != "adaptive":
        return
    state = dict(source.polling_state or {})
    pending = min(1000, int(state.pop("pending_arrivals", 0)) + arrivals)
    if not automatic and state.get("observed_at"):
        state["pending_arrivals"] = pending
        source.polling_state = state
        return
    previous = resolve_interval(source)
    source.polling_state = decide(
        state,
        now=now,
        arrivals=pending,
        minimum=settings.adaptive_source_polling_min_seconds,
        maximum=settings.adaptive_source_polling_max_seconds,
    )
    if previous != resolve_interval(source):
        logger.info(
            "source_polling_interval_changed",
            extra={
                "source_id": source.id,
                "previous_interval_seconds": previous,
                "interval_seconds": resolve_interval(source),
                "decision_reason": source.polling_state["reason"],
            },
        )
