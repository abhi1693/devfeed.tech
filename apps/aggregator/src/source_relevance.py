"""Bounded source relevance inference through the existing Codex client."""

from devfeed_core.config import get_settings
from devfeed_core.feeds.validation import validate_feed
from devfeed_core.inference_validation import feedback_prompt
from devfeed_core.models import utcnow
from devfeed_core.source_relevance import (
    VERSION,
    SourceRelevance,
    approval_supported,
    feed_sample,
    relevance_prompt,
)

from devfeed_aggregator.codex_client import CodexClient


def assess_source(feed_url, source_type, *, feedback=None):
    parsed = validate_feed(feed_url, source_type=source_type)
    sample = feed_sample(parsed)
    settings = get_settings()
    base = {
        "version": VERSION,
        "feed_url": feed_url,
        "source_type": source_type,
        "checked_at": utcnow().isoformat(),
        "sample": sample,
        "approval_supported": False,
        "content_not_before": (
            settings.ai_content_not_before.isoformat() if settings.ai_content_not_before else None
        ),
    }
    if len(sample) < 3:
        return {
            **base,
            "relevance": "uncertain",
            "reason": (
                "Fewer than three feed entries with source publication dates "
                "in the AI content window."
                if settings.ai_content_not_before
                else "Fewer than three usable feed entries."
            ),
        }
    schema = SourceRelevance.model_json_schema()
    schema["$defs"]["EntryRelevance"]["properties"]["index"]["enum"] = [
        entry["index"] for entry in sample
    ]
    schema["properties"]["entries"].update(minItems=len(sample), maxItems=len(sample))
    client = CodexClient(settings)
    client.operation = "source_relevance"
    output = client.complete(relevance_prompt(sample) + feedback_prompt(feedback), schema)
    result = SourceRelevance.model_validate(output)
    return {
        **base,
        **result.model_dump(mode="json"),
        "model": getattr(client, "model", settings.codex_model),
        "approval_supported": approval_supported(result, sample),
    }
