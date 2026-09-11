"""Bounded source relevance inference through the existing Codex client."""

from devfeed_core.config import get_settings
from devfeed_core.feeds.validation import validate_feed
from devfeed_core.models import utcnow
from devfeed_core.source_relevance import (
    VERSION,
    SourceRelevance,
    approval_supported,
    feed_sample,
    relevance_prompt,
)

from devfeed_aggregator.codex_client import CodexClient


def assess_source(feed_url, source_type):
    parsed = validate_feed(feed_url, source_type=source_type)
    sample = feed_sample(parsed)
    base = {
        "version": VERSION,
        "feed_url": feed_url,
        "source_type": source_type,
        "checked_at": utcnow().isoformat(),
        "sample": sample,
        "approval_supported": False,
    }
    if len(sample) < 3:
        return {**base, "relevance": "uncertain", "reason": "Fewer than three usable feed entries."}
    settings = get_settings()
    output = CodexClient(settings).complete(
        relevance_prompt(sample), SourceRelevance.model_json_schema()
    )
    result = SourceRelevance.model_validate(output)
    return {
        **base,
        **result.model_dump(mode="json"),
        "model": settings.codex_model,
        "approval_supported": approval_supported(result, sample),
    }
