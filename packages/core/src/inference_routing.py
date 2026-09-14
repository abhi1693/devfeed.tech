"""Explicit per-task routes. Validation, not model confidence, permits escalation."""

from dataclasses import dataclass

from devfeed_core.config import Settings


@dataclass(frozen=True)
class Route:
    model: str | None
    effort: str | None
    escalated: bool = False


def route_for(settings: Settings, operation: str, *, quality_failure: bool = False) -> Route:
    if not settings.ai_tiered_routing_enabled:
        return Route(settings.codex_model, None)
    if quality_failure:
        return Route(settings.ai_escalation_model, "medium", True)
    research = operation in {
        "topic_research",
        "topic_discovery",
        "relationship_research",
        "research_verification",
        "relationship_verification",
        "topic_verification",
    }
    return Route(
        settings.ai_research_model if research else settings.ai_fast_model,
        "medium" if research else "low",
    )


def total_tokens(usage: dict) -> int:
    """Cached input is included in input; reasoning is included in output."""

    def count(key):
        value = usage.get(key, 0)
        return value if type(value) is int and value >= 0 else 0

    return max(count("totalTokens"), count("inputTokens") + count("outputTokens"))
