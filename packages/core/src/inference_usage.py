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

import psycopg

from devfeed_core.config import Settings
from devfeed_core.inference_ledger import InferenceLedger
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


def _failure_fields(values: JsonRecord, error: Exception, attempt: int) -> dict[str, object]:
    try:
        identity = str(uuid.UUID(str(values.get("id"))))
    except (ValueError, TypeError, AttributeError):
        identity = "invalid"
    return {"inference_id": identity, "attempt": attempt, "error_type": type(error).__name__}


async def record_call(values: JsonRecord, *, settings: Settings) -> None:
    """Retry one stable observation; paid inference itself is never replayed."""
    attempt = 0
    observation = values
    try:
        observation = deepcopy(values)
        store = InferenceLedger(settings)
        # Retries share the existing single-request budget instead of
        # multiplying the maximum wait by the number of delivery attempts.
        async with asyncio.timeout(settings.database_connect_timeout_seconds):
            for number, delay in enumerate(PERSISTENCE_DELAYS, 1):
                if delay:
                    await asyncio.sleep(delay)
                attempt = number
                try:
                    await store.write(observation)
                except Exception as error:
                    if attempt == len(PERSISTENCE_DELAYS) or not isinstance(
                        error, (psycopg.OperationalError, TimeoutError)
                    ):
                        raise
                else:
                    return
    except Exception as error:
        logger.error(
            "inference_usage_persistence_failed", extra=_failure_fields(observation, error, attempt)
        )


def call_id() -> uuid.UUID:
    return uuid.uuid4()
