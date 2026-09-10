"""Transaction-free state transitions for durable jobs; callers enforce eligibility."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


class LeasedJob(Protocol):
    status: str
    attempts: int
    available_at: datetime
    dispatched_at: datetime | None
    lease_until: datetime | None
    lease_token: uuid.UUID | None
    finished_at: datetime | None
    error: str | None


class ResultJob(LeasedJob, Protocol):
    outcome: str | None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: int = 30
    max_retry_after: int | None = None

    def delay(self, attempts: int, retry_after: int = 0) -> float:
        if self.max_retry_after is not None:
            retry_after = min(self.max_retry_after, retry_after)
        return max(self.initial_delay * 2 ** (attempts - 1), retry_after)


DEFAULT_RETRY = RetryPolicy()
VERIFICATION_RETRY = RetryPolicy(initial_delay=300, max_retry_after=86400)


def clear_lease(job: LeasedJob) -> None:
    job.lease_token = job.lease_until = None


def start_job(job: LeasedJob, now: datetime, lease_seconds: int) -> uuid.UUID:
    job.status = "running"
    job.attempts += 1
    token = job.lease_token = uuid.uuid4()
    job.lease_until = now + timedelta(seconds=lease_seconds)
    return token


def finish_job(job: ResultJob, outcome: str, now: datetime) -> None:
    job.status, job.outcome = "succeeded", outcome
    job.finished_at = now
    clear_lease(job)
    job.error = None


def fail_or_retry(
    job: LeasedJob,
    error: str,
    now: datetime,
    *,
    retryable: bool = True,
    retry_after: int = 0,
    policy: RetryPolicy = DEFAULT_RETRY,
    attempts: int | None = None,
) -> None:
    job.error = error[:1000]
    clear_lease(job)
    job.dispatched_at = None
    effective_attempts = job.attempts if attempts is None else attempts
    if retryable and effective_attempts < policy.max_attempts:
        job.status = "queued"
        job.available_at = now + timedelta(seconds=policy.delay(effective_attempts, retry_after))
    else:
        job.status = "failed"
        job.finished_at = now
