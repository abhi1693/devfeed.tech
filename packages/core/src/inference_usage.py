"""Append-only, content-free accounting for each actual inference invocation."""

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.dialects.postgresql import insert

from devfeed_core.db import get_engine
from devfeed_core.models import InferenceCall

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


def record_call(values: dict) -> None:
    # Usage persistence must not turn a successful inference into a costly retry.
    # Failures are explicit in logs; no prompts, responses or credentials are retained.
    try:
        with get_engine().begin() as connection:
            from sqlalchemy import text

            connection.execute(text("SET LOCAL statement_timeout='3s'"))
            connection.execute(text("SET LOCAL lock_timeout='1s'"))
            statement = insert(InferenceCall).values(**values)
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[InferenceCall.id],
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in (
                            "finished_at",
                            "reasoning_effort",
                            "status",
                            "tokens",
                            "web_searches",
                            "duration_ms",
                        )
                    },
                    where=(InferenceCall.status == "running")
                    & (statement.excluded.status != "running"),
                )
            )
    except Exception:
        logger.error(
            "inference_usage_persistence_failed", extra={"inference_id": str(values["id"])}
        )


def call_id() -> uuid.UUID:
    return uuid.uuid4()
