"""Short-lived public page reuse; never reuse model verdicts or renew evidence age."""

import hashlib
import json
import time
from datetime import datetime

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import utcnow
from devfeed_core.research_evidence import fetched_page
from devfeed_core.urls import validate_public_url


def reusable_page(url: str, timeout: float) -> dict:
    from devfeed_core.cache import get_cache

    started = time.monotonic()
    validate_public_url(url)
    settings = get_settings()
    ttl = min(settings.topic_evidence_reuse_seconds, settings.topic_evidence_max_age_seconds)
    cache, key = None, None
    if settings.cache_enabled and ttl:
        try:
            cache = get_cache()
            key = cache.namespace + ":topic-evidence:v1:" + hashlib.sha256(url.encode()).hexdigest()
            raw = cache.redis.get(key)
            if raw and len(raw) <= settings.cache_max_bytes:
                page = json.loads(raw)
                age = (utcnow() - datetime.fromisoformat(page["validated_at"])).total_seconds()
                if (
                    0 <= age < min(ttl, int(page.get("reuse_seconds", ttl)))
                    and page.get("reusable") is True
                    and all(
                        isinstance(page.get(k), str) for k in ("text", "final_url", "content_hash")
                    )
                ):
                    validate_public_url(page["final_url"])
                    return page
        except Exception:
            cache = None
    # The normal fetcher validates DNS, redirects, response size and the download deadline.
    # Expired evidence and unavailable caches never authorize stale fallback.
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise FeedError("Evidence deadline exceeded", reason="verification_timeout", retryable=True)
    page = fetched_page(url, remaining)
    if cache is not None and key and page.get("reusable") is True:
        try:
            raw = json.dumps(page).encode()
            if len(raw) <= settings.cache_max_bytes:
                page_ttl = min(ttl, int(page.get("reuse_seconds", ttl)))
                if page_ttl > 0:
                    cache.redis.set(key, raw, ex=page_ttl)
        except Exception:
            pass
    return page
