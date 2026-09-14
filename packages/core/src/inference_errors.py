"""Shared inference failures, independent of the transport or worker package."""


class AnalysisError(Exception):
    """Safe bounded error codes; never includes a server payload or credential."""

    def __init__(self, code: str, *, retry_after: int = 0):
        super().__init__(code)
        self.retry_after = retry_after
