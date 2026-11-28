"""Normalize content-free accounting fields from an inference invocation."""

import re
import uuid
from datetime import UTC, datetime

from devfeed_core.json_types import JsonRecord


def utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid inference start time") from error
    if not isinstance(value, datetime):
        raise ValueError("Invalid inference start time")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _number(value: object) -> int:
    raw = str(value)
    return int(raw) if re.fullmatch(r"[0-9]{1,12}", raw) else 0


def observation(record: JsonRecord) -> JsonRecord:
    if not isinstance(record, dict):
        raise ValueError("Inference observation must be an object")
    try:
        identity = str(uuid.UUID(str(record["id"])))
        started = utc_timestamp(record["started_at"])
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("Inference observation needs a valid identity and start time") from error
    status = record.get("status") or "unknown"
    if not isinstance(status, str):
        raise ValueError("Invalid inference status")
    task, model, effort = (
        record.get("operation") or "unknown",
        record.get("model") or "Unknown model",
        record.get("reasoning_effort") or "Inherited / unknown",
    )
    if any(
        not isinstance(value, str) or len(value) > 200 for value in (status, task, model, effort)
    ):
        raise ValueError("Invalid inference dimensions")
    tokens = record.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise ValueError("Invalid inference token counters")
    input_tokens, output_tokens = (
        _number(tokens.get("inputTokens")),
        _number(tokens.get("outputTokens")),
    )
    try:
        searches = max(int(record.get("web_searches") or 0), 0)
        duration = int(record["duration_ms"]) if record.get("duration_ms") is not None else None
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Invalid inference counters") from error
    if searches >= 2**64 or (duration is not None and duration >= 2**64):
        raise ValueError("Inference counter exceeds storage range")
    return {
        "id": identity,
        "started_at": started,
        "revision": int(status != "running"),
        "task": task,
        "model": model,
        "effort": effort,
        "model_is_null": int(record.get("model") is None),
        "model_is_empty": int(record.get("model") == ""),
        "effort_is_null": int(record.get("reasoning_effort") is None),
        "effort_is_empty": int(record.get("reasoning_effort") == ""),
        "reason": record.get("reason")
        if isinstance(record.get("reason"), str)
        and record.get("reason") in {"source_review", "verify_research"}
        else None,
        "status": status,
        "input_tokens": input_tokens,
        "cached_input_tokens": min(input_tokens, _number(tokens.get("cachedInputTokens"))),
        "reported_cached_input_tokens": _number(tokens.get("cachedInputTokens")),
        "cache_write_input_tokens": _number(tokens.get("cacheWriteInputTokens")),
        "has_input_tokens": int("inputTokens" in tokens),
        "output_tokens": output_tokens,
        "reasoning_tokens": min(output_tokens, _number(tokens.get("reasoningOutputTokens"))),
        "web_searches": searches,
        "duration_ms": max(duration or 0, 0),
        "has_duration": int(duration is not None and duration >= 0),
    }
