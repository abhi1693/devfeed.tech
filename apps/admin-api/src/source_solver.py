"""Bounded, transient form requests to the isolated browser worker."""

import base64
import time
from contextlib import suppress

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.redis import create_redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from rq.serializers import JSONSerializer


def solve_feed(url: str) -> FetchResult:
    settings = get_settings()
    if not settings.solver_queue_enabled:
        raise FeedError(
            "The solver is not enabled for this environment.", reason="solver_unavailable"
        )
    job = None
    try:
        with create_redis(
            settings,
            socket_timeout=3,
            socket_connect_timeout=3,
            retry=Retry(NoBackoff(), retries=0),
        ) as client:
            queue = Queue("solver", connection=client, serializer=JSONSerializer)
            job = queue.enqueue(
                "devfeed_core.feeds.solvers.solve_feed_request",
                url,
                job_timeout=80,
                ttl=15,
                result_ttl=120,
                failure_ttl=120,
                description="Explicit source form feed verification",
            )
            deadline = time.monotonic() + 95
            try:
                while time.monotonic() < deadline:
                    status = job.get_status(refresh=True)
                    if status == JobStatus.FINISHED:
                        result = job.return_value()
                        if not isinstance(result, dict) or result.get("error"):
                            reason = result.get("error") if isinstance(result, dict) else None
                            raise FeedError(
                                "The solver could not retrieve this feed. "
                                "Try again later or use another feed URL.",
                                reason="browser_challenge"
                                if reason == "browser_challenge"
                                else "solver_unavailable",
                            )
                        body = base64.b64decode(result["body"], validate=True)
                        if len(body) > settings.feed_max_bytes:
                            raise FeedError(
                                "Solver response is too large.", reason="response_too_large"
                            )
                        return FetchResult(200, body, result["url"])
                    if status in {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STOPPED}:
                        break
                    time.sleep(0.25)
            finally:
                # Running work remains bounded by its worker timeout; queued
                # abandoned requests must not consume browser capacity later.
                with suppress(RedisError, NoSuchJobError):
                    if job.get_status(refresh=True) == JobStatus.QUEUED:
                        job.cancel()
                    if job.get_status(refresh=True) != JobStatus.STARTED:
                        job.delete()
    except (RedisError, NoSuchJobError) as exc:
        raise FeedError(
            "The solver is unavailable. Try again later.", reason="solver_unavailable"
        ) from exc
    raise FeedError(
        "The solver did not finish in time. Try again later.", reason="solver_unavailable"
    )
