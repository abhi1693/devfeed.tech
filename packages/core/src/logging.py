"""Shared, bounded text/JSON logging. Preserve supplied messages and fields."""

import json
import logging
import math
import queue
import re
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from devfeed_core.log_text import context_text, error_text, event_text, inline, local_time

_context: ContextVar[dict | None] = ContextVar("devfeed_log_context", default=None)
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,100}\Z")
_APP_LOGGERS = (
    "devfeed_core.",
    "devfeed_api.",
    "devfeed_admin_api.",
    "devfeed_user_api.",
    "devfeed_cli.",
    "devfeed_aggregator.",
    "devfeed_notifications.",
)
# Exclude logging machinery, not application-provided fields.
_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_RESERVED_FIELDS = {"timestamp", "level", "logger", "event", "pid", "exception"}


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


def log_value(value, depth: int = 0):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (UUID, datetime)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and depth < 3:
        return [log_value(item, depth + 1) for item in value[:30]]
    if isinstance(value, dict) and depth < 3:
        return {str(key): log_value(item, depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, (list, tuple, dict)):
        return "[truncated]"
    return str(value)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.payload(record), ensure_ascii=True, allow_nan=False)

    def payload(self, record: logging.LogRecord) -> dict:
        from devfeed_core.telemetry import trace_fields

        fields = {**(_context.get() or {}), **record.__dict__, **trace_fields()}
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
                key: log_value(value)
                for key, value in fields.items()
                if key not in _RECORD_FIELDS and key not in _RESERVED_FIELDS
            },
        }
        if payload["event"] == "dependency_log":
            payload["message"] = record.getMessage()
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
    """Preserve exception messages, chained causes and bounded stack locations."""
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
        detail = {"type": type(exc).__name__, "message": str(exc), "frames": frames[-30:]}
        # Preserve interpreter-supplied identifiers without inspecting the object.
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


class QueuedStderrHandler(StderrHandler):
    """Bounded API log output: a stalled collector must not stall the event loop.

    Format on the caller to preserve ContextVars and serialize records before they
    enter the queue. Only bounded formatted strings reach the output thread.
    When full, discard new logs rather than block requests or grow memory forever.
    """

    def __init__(self, capacity: int = 1024):
        super().__init__()
        self.pending: queue.Queue = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.dropped = 0
        self.worker = threading.Thread(target=self._write_logs, name="api-log-output", daemon=True)
        self.worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self.stopping.is_set():
            return
        if isinstance(self.formatter, TextFormatter) and self.formatter.quiet(record):
            return
        try:
            line = self.format(record)
        except Exception:
            # Do not use StreamHandler's synchronous/raw-record error fallback.
            line = "logging_error: unable to format application log"
        try:
            self.pending.put_nowait((sys.stderr, line))
        except queue.Full:
            self.dropped += 1

    def _write_logs(self) -> None:
        while not self.stopping.is_set() or not self.pending.empty():
            try:
                item = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if isinstance(item, threading.Event):
                    item.set()
                else:
                    stream, line = item
                    with suppress(OSError, ValueError):
                        stream.write(line + "\n")
                        stream.flush()
            finally:
                self.pending.task_done()

    def flush(self) -> None:
        # Explicit drains (tests/process shutdown) are bounded even if stderr hangs.
        marker = threading.Event()
        try:
            self.pending.put(marker, timeout=1)
        except queue.Full:
            return
        marker.wait(timeout=1)

    def close(self) -> None:
        self.stopping.set()
        if threading.current_thread() is not self.worker:
            self.worker.join(timeout=1)
        super().close()


def configure_logging(
    service: str, level: str = "INFO", log_format: str = "text", *, non_blocking: bool = False
) -> None:
    """Idempotent process entrypoint setup; leave unrelated embedding/test handlers alone."""
    root = logging.getLogger()
    handler = next((item for item in root.handlers if isinstance(item, StderrHandler)), None)
    if handler is not None and (
        isinstance(handler, QueuedStderrHandler) != non_blocking
        or isinstance(handler, QueuedStderrHandler)
        and handler.stopping.is_set()
    ):
        root.removeHandler(handler)
        handler.close()
        handler = None
    if handler is None:
        handler = QueuedStderrHandler() if non_blocking else StderrHandler()
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
        "devfeed_user_api",
        "devfeed_cli",
        "devfeed_aggregator",
        "devfeed_notifications",
    ):
        logging.getLogger(namespace).setLevel(level)
    # The application supplies its own request logs. Uvicorn's access logs
    # would double-count requests.
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
    for name in ("httpcore", "httpcore2", "httpx", "httpx2", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)
