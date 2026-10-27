"""Append-only, content-free accounting for each actual inference invocation."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypedDict, Unpack

from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.json_types import JsonRecord

logger = logging.getLogger(__name__)


class InferenceContext(TypedDict, total=False):
    operation: str
    job_id: uuid.UUID | str | None
    attempt: int | None
    reason: str | None


_context: ContextVar[InferenceContext | None] = ContextVar("inference_usage_context", default=None)


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


async def record_call(values: JsonRecord, *, client: AnalyticsClient) -> None:
    """Persist content-free usage without causing successful paid work to retry."""
    try:
        await InferenceStore(client).write([values])
    except Exception:
        logger.error(
            "inference_usage_persistence_failed", extra={"inference_id": str(values["id"])}
        )


def call_id() -> uuid.UUID:
    return uuid.uuid4()
