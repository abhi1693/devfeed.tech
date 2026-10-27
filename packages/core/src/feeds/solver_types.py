"""Provider-neutral contract for external challenge solvers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from devfeed_core.feeds.fetcher import FeedError, FetchResult


@dataclass(frozen=True)
class SolveRequest:
    url: str
    timeout_seconds: float
    max_bytes: int
    limit_setting: str
    image: bool = False


class Solver(Protocol):
    def solve(self, request: SolveRequest) -> FetchResult: ...


class SolverUnavailable(FeedError):
    def __init__(
        self,
        *,
        work_may_continue: bool = False,
        stage: Literal[
            "unspecified",
            "http",
            "deadline",
            "connect",
            "transport",
            "invalid_response",
            "coordination",
        ] = "unspecified",
        http_status: int | None = None,
    ) -> None:
        super().__init__(
            "Solver service unavailable",
            reason="solver_unavailable",
            retryable=True,
            retry_after=60,
            status=http_status,
        )
        self.work_may_continue = work_may_continue
        self.stage = stage
