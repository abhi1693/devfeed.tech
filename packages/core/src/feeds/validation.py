import logging
import time
from datetime import UTC, datetime

from devfeed_core.feeds.fetcher import FeedError, fetch_feed
from devfeed_core.feeds.parser import ParsedFeed, parse_feed
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.source_types import SourceType
from devfeed_core.urls import fingerprint

logger = logging.getLogger(__name__)


class FeedValidationError(ValueError):
    def __init__(self, cause: FeedError):
        super().__init__(f"Feed validation failed: {cause}")
        self.upstream_status = cause.status
        self.retryable = cause.retryable


def validate_feed(url: str, *, source_type: SourceType) -> ParsedFeed:
    """Require a full, readable response before admission; do not persist validators.

    ETags/Last-Modified must not be saved here: no articles have been ingested yet,
    so a subsequent 304 must not cause the initial worker run to skip them.
    """
    with log_context(feed_id=fingerprint(url), source_type=source_type):
        return _validate_feed(url, source_type)


def _validate_feed(url: str, source_type: SourceType) -> ParsedFeed:
    started = time.perf_counter()
    logger.info("feed_validation_started")
    try:
        result = fetch_feed(url)  # Deliberately unconditional: validation needs a body.
        if result.status != 200:
            raise FeedError(
                "Validation requires HTTP 200 with a feed body",
                status=result.status,
                reason="missing_body",
            )
        parsed = parse_feed(
            result.body, result.final_url, datetime.now(UTC), source_type=source_type
        )
        if parsed.seen and not parsed.entries:
            raise FeedError(
                "Feed contains entries, but none have a usable article title and URL",
                reason="unusable_entries",
            )
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
    return parsed
