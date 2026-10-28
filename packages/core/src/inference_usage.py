"""Append-only, content-free accounting for each actual inference invocation."""

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_inference_store import InferenceStore

logger = logging.getLogger(__name__)
_context: ContextVar[dict | None] = ContextVar("inference_usage_context", default=None)


@contextmanager
def inference_context(**values):
    token = _context.set({**(_context.get() or {}), **values})
    try:
        yield
    finally:
        _context.reset(token)


def context() -> dict:
    return dict(_context.get() or {})


def request_hash(prompt: str, schema: dict) -> str:
    return hashlib.sha256(
        json.dumps([prompt, schema], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def record_call(values: dict, *, client: AnalyticsClient) -> None:
    """Persist content-free usage without causing successful paid work to retry."""
    try:
        await InferenceStore(client).write([values])
    except Exception:
        logger.error(
            "inference_usage_persistence_failed", extra={"inference_id": str(values["id"])}
        )


def call_id() -> uuid.UUID:
    return uuid.uuid4()
