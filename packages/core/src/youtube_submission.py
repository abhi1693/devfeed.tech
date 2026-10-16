"""Submission previews expose explicit resource choices without expanding a watch URL."""

from typing import get_args

from pydantic import BaseModel, Field

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.schemas import InputModel, YouTubeTargetKind


class ResourceChoice(BaseModel):
    target: YouTubeTargetKind
    canonical_url: str
    name: str | None = None


class ResourcePreview(InputModel):
    provider: str = "rss"
    resource_kind: str = "feed"
    canonical_url: str | None = None
    resources: list[ResourceChoice] = Field(default_factory=list)


def resource_choices(url: str) -> list[ResourceChoice]:
    from devfeed_core.youtube import parse_youtube_url

    choices = []
    for kind in get_args(YouTubeTargetKind):
        try:
            target = parse_youtube_url(url, target=kind)
        except ValueError:
            continue
        if target is not None and target.kind == kind:
            choices.append(ResourceChoice(target=kind, canonical_url=target.canonical_url))
    return sorted(
        choices, key=lambda choice: {"video": 0, "playlist": 1, "channel": 2}[choice.target]
    )


def provider_error(exc: FeedError) -> tuple[int, str]:
    if exc.reason in {"missing_configuration", "youtube_not_configured"}:
        return (
            503,
            "YouTube indexing is not configured. "
            "Ask an administrator to configure the provider API key.",
        )
    if exc.reason in {
        "quota_exhausted",
        "rate_limited",
        "youtube_quota_exhausted",
        "youtube_request_budget",
    }:
        return (
            503,
            "YouTube is temporarily unavailable because its API quota or rate limit was reached. "
            "Try again later.",
        )
    return (
        422,
        "YouTube metadata could not be retrieved. "
        "Check that the selected resource is public and try again.",
    )
