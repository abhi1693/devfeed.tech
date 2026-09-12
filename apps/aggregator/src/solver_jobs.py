"""Persist solver handoffs in the existing PostgreSQL outbox, never in worker RAM."""

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease
from devfeed_core.models import ArticleEnrichmentJob, ArticleImageJob, SourceEnrichmentJob, utcnow


def defer_to_solver(
    job: ArticleEnrichmentJob | ArticleImageJob | SourceEnrichmentJob,
    error: FeedError | None,
    message: str,
) -> bool:
    settings = get_settings()
    if (
        error is None
        or error.reason != "browser_challenge"
        or not settings.solver_queue_enabled
        or settings.solver_services
    ):
        return False
    job.requires_solver = True
    job.status = "queued"
    job.available_at = utcnow()
    job.dispatched_at = None
    job.finished_at = None
    job.error = message[:1000]
    # Routing to another worker is not a failed solver attempt.
    job.attempts = max(0, job.attempts - 1)
    clear_lease(job)
    return True
