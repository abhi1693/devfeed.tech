"""Append-only, content-free accounting for each actual inference invocation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import TypedDict, Unpack

from aiohttp import ClientConnectionError, ClientPayloadError, ClientResponseError
from clickhouse_connect.driver.exceptions import Error as ClickHouseError
from clickhouse_connect.driver.exceptions import OperationalError

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.json_types import JsonRecord

logger = logging.getLogger(__name__)


class InferenceContext(TypedDict, total=False):
    operation: str
    job_id: uuid.UUID | str | None
    attempt: int | None
    reason: str | None


_context: ContextVar[InferenceContext | None] = ContextVar("inference_usage_context", default=None)
PERSISTENCE_DELAYS = (0.0, 0.25, 0.5)


@contextmanager
def inference_context(**values: Unpack[InferenceContext]) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **values})
    try:
        yield
    finally:
        _context.reset(token)


def context() -> InferenceContext:
    value: InferenceContext = _context.get() or {}
    return value.copy()


def request_hash(prompt: str, schema: JsonRecord) -> str:
    return hashlib.sha256(
        json.dumps([prompt, schema], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _cause(error: BaseException) -> BaseException:
    for _ in range(4):
        if not isinstance(error, AnalyticsUnavailable) or error.__cause__ is None:
            break
        error = error.__cause__
    return error


def _retryable(error: Exception) -> bool:
    cause = _cause(error)
    if isinstance(cause, ClientResponseError):
        return cause.status in {408, 429} or 500 <= cause.status < 600
    if isinstance(cause, (ClientConnectionError, ClientPayloadError, TimeoutError)):
        return True
    if isinstance(cause, OperationalError) and cause.code is None:
        return True
    if isinstance(cause, ClickHouseError):
        return cause.code in {202, 241}
    return isinstance(cause, AnalyticsUnavailable)


def _failure_fields(values: JsonRecord, error: Exception, attempt: int) -> dict[str, object]:
    try:
        identity = str(uuid.UUID(str(values.get("id"))))
    except (ValueError, TypeError, AttributeError):
        identity = "invalid"
    cause = _cause(error)
    fields: dict[str, object] = {
        "inference_id": identity,
        "attempt": attempt,
        "error_type": type(error).__name__,
        "cause_type": type(cause).__name__,
    }
    if isinstance(cause, ClickHouseError) and type(cause.code) is int:
        fields["clickhouse_code"] = cause.code
    return fields


async def record_call(values: JsonRecord, *, client: AnalyticsClient) -> None:
    """Retry one stable observation; paid inference itself is never replayed."""
    attempt = 0
    observation = values
    try:
        observation = deepcopy(values)
        store = InferenceStore(client)
        # Retries share the existing single-request budget instead of
        # multiplying the maximum wait by the number of delivery attempts.
        async with asyncio.timeout(client.settings.clickhouse_timeout_seconds):
            for number, delay in enumerate(PERSISTENCE_DELAYS, 1):
                if delay:
                    await asyncio.sleep(delay)
                attempt = number
                try:
                    await store.write([observation])
                except Exception as error:
                    if attempt == len(PERSISTENCE_DELAYS) or not _retryable(error):
                        raise
                else:
                    return
    except Exception as error:
        logger.error(
            "inference_usage_persistence_failed", extra=_failure_fields(observation, error, attempt)
        )


def call_id() -> uuid.UUID:
    return uuid.uuid4()
