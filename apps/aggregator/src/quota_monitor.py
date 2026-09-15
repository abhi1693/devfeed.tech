"""Refresh shared quota without running inference or blocking background queues."""

import json
import logging
import time

from devfeed_core.config import get_settings
from devfeed_core.quota_pacing import paced_snapshot, quota_key
from devfeed_core.redis import create_redis

from devfeed_aggregator.codex_client import CodexClient

logger = logging.getLogger(__name__)


def refresh_quota():
    settings = get_settings()
    if not settings.ai_enabled or not settings.ai_quota_pacing_enabled:
        return
    try:
        with create_redis(settings) as connection:
            # This timestamp gate bounds account RPCs even with multiple schedulers.
            if not connection.set(quota_key() + ":poll", "1", nx=True, ex=60):
                return
            raw = connection.get(quota_key())
            previous = json.loads(raw) if raw else None
            snapshot = paced_snapshot(
                previous,
                CodexClient(settings).read_quota(),
                now=time.time(),
                reserve=settings.ai_quota_reserve_percent,
            )
            if snapshot is not None:
                # Retain the daily ceiling through service restarts. Freshness is
                # checked separately, so retained data never grants stale admission.
                connection.set(quota_key(), json.dumps(snapshot), ex=8 * 86400)
    except Exception:
        logger.warning("codex_quota_check_failed")
