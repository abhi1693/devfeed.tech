"""Provider-neutral contract for external challenge solvers."""

from dataclasses import dataclass
from typing import Protocol

from devfeed_core.feeds.fetcher import FeedError, FetchResult


@dataclass(frozen=True)
class SolveRequest:
    url: str
    timeout_seconds: float
    max_bytes: int
    limit_setting: str


class Solver(Protocol):
    def solve(self, request: SolveRequest) -> FetchResult: ...


class SolverUnavailable(FeedError):
    def __init__(self, *, work_may_continue: bool = False):
        super().__init__(
            "Solver service unavailable",
            reason="solver_unavailable",
            retryable=True,
            retry_after=60,
        )
        self.work_may_continue = work_may_continue
