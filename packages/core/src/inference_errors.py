"""Shared inference failures, independent of the transport or worker package."""

from __future__ import annotations


class AnalysisError(Exception):
    """Safe bounded error codes; never includes a server payload or credential."""

    def __init__(self, code: str, *, retry_after: int = 0) -> None:
        super().__init__(code)
        self.retry_after = retry_after
