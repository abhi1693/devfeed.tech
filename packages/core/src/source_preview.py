"""Read-only source preflight. No database sessions, queues, or saved validators."""

import logging
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from devfeed_core.feeds.fetcher import FeedError, fetch_page
from devfeed_core.feeds.validation import validate_feed
from devfeed_core.source_profiles import PROFILE_FIELDS, SourceProfile, website_profile
from devfeed_core.source_types import SourceType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourcePreview:
    name: str
    profile: SourceProfile
    entries_seen: int
    entries_skipped: int
    warnings: tuple[str, ...] = ()


def preview_source(feed_url: str, source_type: SourceType) -> SourcePreview:
    parsed = validate_feed(feed_url, source_type=source_type)
    values = asdict(parsed.profile)
    warnings: tuple[str, ...] = ()
    # Only follow the feed's declared website, never guess a favicon, brand,
    # submitter, or source type. The transport checks DNS and every redirect.
    if parsed.profile.website_url and any(not values[field] for field in PROFILE_FIELDS):
        try:
            page = website_profile(fetch_page(parsed.profile.website_url))
            for field in PROFILE_FIELDS:
                values[field] = values[field] or getattr(page, field)
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
    )
