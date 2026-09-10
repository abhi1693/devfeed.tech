"""Shared, bounded text/JSON logging. Never pass input payloads as log fields."""

import json
import logging
import math
import re
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from devfeed_core.http_logging import safe_request_url
from devfeed_core.log_text import context_text, error_text, event_text, inline, local_time

_context: ContextVar[dict | None] = ContextVar("devfeed_log_context", default=None)
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,100}\Z")
_APP_LOGGERS = (
    "devfeed_core.",
    "devfeed_api.",
    "devfeed_admin_api.",
    "devfeed_cli.",
    "devfeed_aggregator.",
    "devfeed_notifications.",
)
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+")
# Explicit fields prevent accidental logging of request bodies, SQL or settings.
_FIELDS = frozenset(
    [
        "service",
        "request_id",
        "command_id",
        "command",
        "action",
        "job_id",
        "job_kind",
        "rq_job_id",
        "source_id",
        "article_id",
        "image_method",
        "outcome",
        "images_dispatched",
        "images_recovered",
        "profiles_dispatched",
        "profiles_recovered",
        "articles_dispatched",
        "articles_recovered",
        "analyses_dispatched",
        "analyses_recovered",
        "tags_scanned",
        "tags_linked",
        "tags_unlinked",
        "tags_ambiguous",
        "relationship_jobs_scheduled",
        "relationship_scans_completed",
        "approval_status",
        "cache_status",
        "cache_bypass_reason",
        "source_type",
        "worker_name",
        "tick_id",
        "feed_id",
        "method",
        "route",
        "request_url",
        "status_code",
        "duration_ms",
        "error_type",
        "upstream_status",
        "retryable",
        "attempt",
        "entries_seen",
        "entries_skipped",
        "articles_created",
        "articles_updated",
        "languages_detected",
        "languages_unknown",
        "dry_run",
        "scheduled",
        "dispatched",
        "recovered",
        "bytes_received",
        "redirects",
        "enabled",
        "sources_created",
        "submitted",
        "job_status",
        "available_at",
        "category_id",
        "tag_id",
        "parent_id",
        "changed_fields",
        "burst",
        "max_jobs",
        "queue",
        "dependency",
        "exit_code",
        "reason",
        "phase",
    ]
)


def log_identifier(value: UUID | str) -> str:
    """Keep request identifiers on one log line, even with a custom log handler."""
    return str(value).replace("\r", "").replace("\n", "")


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


@contextmanager
def log_context(**fields) -> Iterator[None]:
    """ContextVars also propagate from ASGI to FastAPI's synchronous thread pool."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def safe_value(value, depth: int = 0):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (UUID, datetime)):
        return str(value)
    if isinstance(value, str):
        # Request locations have a separate sanitizer; other fields never contain URLs.
        return _URL.sub("[redacted-url]", value)[:250]
    if isinstance(value, (list, tuple)) and depth < 3:
        return [safe_value(item, depth + 1) for item in value[:30]]
    return "[omitted]"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.payload(record), ensure_ascii=True, allow_nan=False)

    def payload(self, record: logging.LogRecord) -> dict:
        fields = {**(_context.get() or {}), **record.__dict__}
        event = (
            record.msg
            if (isinstance(record.msg, str) and record.name.startswith(_APP_LOGGERS))
            else ""
        )
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "event": event if _EVENT.fullmatch(event) else "dependency_log",
            "pid": record.process,
            **{
                key: safe_request_url(value)
                if key in {"route", "request_url"}
                else safe_value(value)
                for key, value in fields.items()
                if key in _FIELDS
            },
        }
        if payload["event"] == "dependency_log":
            # Library messages/arguments can contain SQL, HTTP query strings or RQ tracebacks.
            # Keep their origin, but never format their raw messages or arguments.
            payload["location"] = f"{record.module}.{record.funcName}:{record.lineno}"
        if record.exc_info and record.exc_info[1] is not None:
            payload["error_type"] = type(record.exc_info[1]).__name__
            payload["exception"] = exception_details(record.exc_info[1])
        return payload


class TextFormatter(JsonFormatter):
    def __init__(self, service: str, *, verbose: bool = False):
        super().__init__(service)
        self.verbose = verbose

    def format(self, record: logging.LogRecord) -> str:
        payload = self.payload(record)
        level = "WARN" if payload["level"] == "WARNING" else payload["level"]
        prefix = f"{local_time(payload['timestamp'])} {level:<5} [{inline(payload['service'])}]"
        message = event_text(payload)
        error = error_text(payload, verbose=self.verbose)
        if error:
            message += f" - {error}"
        details = context_text(payload, verbose=self.verbose)
        return f"{prefix} {message}" + (f" ({'; '.join(details)})" if details else "")

    def quiet(self, record: logging.LogRecord) -> bool:
        if self.verbose:
            return False
        if not record.name.startswith(_APP_LOGGERS):
            # App lifecycle logs replace verbose library startup/housekeeping output.
            # RQ's custom exception callback already reports failed jobs safely.
            return record.levelno < logging.WARNING or (
                record.name == "rq.worker" and record.funcName == "handle_exception"
            )
        if not isinstance(record.msg, str):
            return False
        service = getattr(record, "service", (_context.get() or {}).get("service", self.service))
        if service == "cli" and record.msg in {
            "command_started",
            "command_completed",
            "command_succeeded",
            "command_rejected",
            "command_conflict",
            "feed_validation_failed",
        }:
            # CLI stdout carries results; its error line already explains rejections.
            return True
        if record.msg == "rq_job_failed" and record.exc_info and record.exc_info[1]:
            return any(
                (frame["file"], frame["function"])
                in {
                    ("tasks.py", "ingest"),
                    ("image_tasks.py", "enrich_image"),
                    ("source_tasks.py", "enrich_source"),
                }
                for detail in exception_details(record.exc_info[1])
                for frame in detail["frames"]
            )  # ingest() has already logged this exception.
        return False


def exception_details(exc: BaseException | None) -> list[dict]:
    """Preserve exception types and stack locations, never messages, source or locals."""
    details: list[dict] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(details) < 5:
        seen.add(id(exc))
        frames = []
        for frame, lineno in traceback.walk_tb(exc.__traceback__):
            frames.append(
                {
                    "file": Path(frame.f_code.co_filename).name,
                    "line": lineno,
                    "function": frame.f_code.co_name,
                }
            )
        detail = {"type": type(exc).__name__, "frames": frames[-30:]}
        # Interpreter-supplied identifiers are useful without the exception
        # message or the object's repr/data. Bound both identifiers.
        if (
            isinstance(exc, AttributeError)
            and isinstance(exc.name, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", exc.name)
        ):
            detail["attribute"] = exc.name
            owner = type(exc.obj).__name__
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", owner):
                detail["object_type"] = owner
        details.append(detail)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return details


class StderrHandler(logging.StreamHandler):
    """Resolve stderr at emit time, including after CLI redirection or an RQ fork."""

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(self.formatter, TextFormatter) and self.formatter.quiet(record):
            return
        self.stream = sys.stderr
        super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:
        # The stdlib fallback prints raw messages/arguments after formatting errors.
        # Never expose the failed record, including when the output stream is closed.
        with suppress(OSError, ValueError):
            sys.stderr.write("logging_error: unable to emit application log\n")


def configure_logging(service: str, level: str = "INFO", log_format: str = "text") -> None:
    """Idempotent process entrypoint setup; leave unrelated embedding/test handlers alone."""
    root = logging.getLogger()
    handler = next((item for item in root.handlers if isinstance(item, StderrHandler)), None)
    if handler is None:
        handler = StderrHandler()
        root.addHandler(handler)
    handler.setFormatter(
        JsonFormatter(service)
        if log_format == "json"
        else TextFormatter(service, verbose=level == "DEBUG")
    )
    handler.setLevel(level)
    root.setLevel(level)
    for namespace in (
        "devfeed_core",
        "devfeed_api",
        "devfeed_admin_api",
        "devfeed_cli",
        "devfeed_aggregator",
        "devfeed_notifications",
    ):
        logging.getLogger(namespace).setLevel(level)
    # The application supplies its own safe request logs. Uvicorn's access logs expose
    # raw paths/query strings and would double-count requests.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in ("uvicorn", "uvicorn.error", "rq", "rq.worker", "rq.job", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.disabled = False
        logger.setLevel(level)
    for name in ("httpcore", "httpx", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
