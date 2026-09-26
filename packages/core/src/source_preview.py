"""Read-only source preflight. No source writes or saved validators."""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.feeds.validation import validate_feed
from devfeed_core.source_inspection import complete_website_profile
from devfeed_core.source_profiles import SourceProfile
from devfeed_core.source_types import SourceType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourcePreview:
    name: str
    profile: SourceProfile
    entries_seen: int
    entries_skipped: int
    warnings: tuple[str, ...] = ()
    final_url: str | None = None


def preview_source(
    feed_url: str, source_type: SourceType, *, solver: Callable[[str], FetchResult] | None = None
) -> SourcePreview:
    parsed = validate_feed(
        feed_url, source_type=source_type, **({"solver": solver} if solver else {})
    )
    values = asdict(parsed.profile)
    warnings: tuple[str, ...] = ()
    # Only follow the feed's declared website, never guess a favicon, brand,
    # submitter, or source type. The transport checks DNS and every redirect.
    if parsed.profile.website_url:
        try:
            values = complete_website_profile(values, parsed.profile.website_url)
        except FeedError as exc:
            logger.warning(
                "source_preview_partial",
                extra={"reason": exc.reason, "upstream_status": exc.status},
            )
            warnings = (
                "The feed is valid, but website details could not be fetched. "
                "You can fill in missing details manually.",
            )
    logger.info("source_preview_completed", extra={"entries_seen": parsed.seen})
    return SourcePreview(
        name=parsed.title or (urlsplit(feed_url).hostname or "")[:200],
        profile=SourceProfile(**values),
        entries_seen=parsed.seen,
        entries_skipped=parsed.skipped,
        warnings=warnings,
        final_url=parsed.final_url,
    )
