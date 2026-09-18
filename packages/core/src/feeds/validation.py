import calendar
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from devfeed_core.feeds.fetcher import FeedError, FetchResult, fetch_feed
from devfeed_core.feeds.parser import ParsedFeed, parse_feed
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.source_types import SourceType
from devfeed_core.urls import fingerprint

logger = logging.getLogger(__name__)


class FeedValidationError(ValueError):
    def __init__(self, cause: FeedError):
        super().__init__(f"Feed validation failed: {cause}")
        self.reason = cause.reason
        self.upstream_status = cause.status
        self.retryable = cause.retryable


def validate_feed(
    url: str, *, source_type: SourceType, solver: Callable[[str], FetchResult] | None = None
) -> ParsedFeed:
    """Require a full, readable response before admission; do not persist validators.

    ETags/Last-Modified must not be saved here: no articles have been ingested yet,
    so a subsequent 304 must not cause the initial worker run to skip them.
    """
    with log_context(feed_id=fingerprint(url), source_type=source_type):
        return _validate_feed(url, source_type, solver)


def _validate_feed(
    url: str, source_type: SourceType, solver: Callable[[str], FetchResult] | None
) -> ParsedFeed:
    started = time.perf_counter()
    logger.info("feed_validation_started")
    try:
        try:
            result = fetch_feed(url)  # Validation always needs a full body.
        except FeedError as exc:
            if solver is None or exc.reason != "browser_challenge":
                raise
            result = solver(url)
        if result.status != 200:
            raise FeedError(
                "Validation requires HTTP 200 with a feed body",
                status=result.status,
                reason="missing_body",
            )
        now = datetime.now(UTC)
        parsed = parse_feed(result.body, result.final_url, now, source_type=source_type)
        if parsed.seen and not parsed.entries:
            raise FeedError(
                "Feed contains entries, but none have a usable article title and URL",
                reason="unusable_entries",
            )
        validate_admission_entries(parsed, now)
    except FeedError as exc:
        logger.warning(
            "feed_validation_failed",
            extra={
                "error_type": type(exc.__cause__ or exc).__name__,
                "reason": exc.reason,
                "upstream_status": exc.status,
                "retryable": exc.retryable,
                "duration_ms": elapsed_ms(started),
            },
        )
        raise FeedValidationError(exc) from exc
    except Exception:
        logger.exception("feed_validation_error", extra={"duration_ms": elapsed_ms(started)})
        raise
    logger.info(
        "feed_validation_succeeded",
        extra={
            "entries_seen": parsed.seen,
            "entries_skipped": parsed.skipped,
            "duration_ms": elapsed_ms(started),
        },
    )
    # Only the transport-validated destination defines feed identity. Do not trust
    # a feed-declared self link or collapse unrelated paths on the same host.
    return replace(parsed, final_url=result.final_url)


def validate_admission_entries(feed: ParsedFeed, now: datetime) -> None:
    """Require useful history and dated activity before accepting a new feed.

    Never use feed_at: undated entries use the fetch time there. Publication
    (or aggregator submission) takes precedence over an update timestamp.
    """
    if len({entry.canonical_url for entry in feed.entries}) < 3:
        raise FeedError(
            "The feed must contain at least 3 distinct usable entries.",
            reason="insufficient_entries",
        )
    month_index = now.year * 12 + now.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    cutoff = now.replace(
        year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1])
    )
    for entry in feed.entries:
        metadata = entry.source_metadata
        value = (
            metadata.get("published_at")
            or metadata.get("submitted_at")
            or metadata.get("updated_at")
        )
        if value and cutoff <= datetime.fromisoformat(value) <= now:
            return
    raise FeedError(
        "The feed must contain at least 1 entry dated within the last 3 months.",
        reason="no_recent_entries",
    )
