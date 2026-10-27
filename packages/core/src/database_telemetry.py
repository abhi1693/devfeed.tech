"""SQLAlchemy instrumentation without SQL text, bind values, or row contents."""

from __future__ import annotations

import logging
import os
import time
from contextlib import AbstractContextManager
from typing import Protocol, cast

from opentelemetry.trace import Span
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext
from sqlalchemy.engine.interfaces import DBAPICursor
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import ConnectionPoolEntry, NullPool, Pool, PoolProxiedConnection, QueuePool

from devfeed_core.telemetry import current, span

logger = logging.getLogger(__name__)


type QueryTrace = tuple[float, str, AbstractContextManager[Span | None], Span | None]


class _ObservedQuery(Protocol):
    _devfeed_query: QueryTrace


class AcquisitionMetrics(Pool):
    # Keep SQLAlchemy lifecycle/debug messages under its existing log policy.
    _sqla_logger_namespace = "sqlalchemy.pool"

    def connect(self) -> PoolProxiedConnection:
        started, result = time.monotonic(), "ok"
        try:
            return super().connect()
        except PoolTimeout:
            result = "timeout"
            raise
        except Exception:
            result = "error"
            raise
        finally:
            if runtime := current():
                elapsed = time.monotonic() - started
                if instrument := runtime.instruments.get("db_wait"):
                    instrument.record(elapsed, {"outcome": result})


class ObservedQueuePool(AcquisitionMetrics, QueuePool):
    pass


class ObservedNullPool(AcquisitionMetrics, NullPool):
    pass


def operation(statement: str) -> str:
    word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
    return word.lower() if word in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "other"


def instrument_engine(engine: Engine) -> None:
    last_slow_log = 0.0

    def before(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: object,
        context: ExecutionContext,
        _many: bool,
    ) -> None:
        if not current():
            return
        verb = operation(statement)
        scope = span(
            "postgresql " + verb,
            attributes={"db.system.name": "postgresql", "db.operation.name": verb},
        )
        active = scope.__enter__()
        observed = cast(_ObservedQuery, context)
        observed._devfeed_query = time.monotonic(), verb, scope, active

    def finish(context: ExecutionContext, error: BaseException | None = None) -> None:
        nonlocal last_slow_log
        value = getattr(context, "_devfeed_query", None)
        if not value:
            return
        delattr(context, "_devfeed_query")
        started, verb, scope, _active = value
        elapsed = time.monotonic() - started
        if (runtime := current()) and (instrument := runtime.instruments.get("database")):
            attributes = {"db.system.name": "postgresql", "db.operation.name": verb}
            if error is not None:
                attributes["error.type"] = "database_error"
            instrument.record(elapsed, attributes)
        # Bounded diagnostic signal with no SQL, parameters or model data.
        if elapsed >= 1 and time.monotonic() - last_slow_log >= 30:
            last_slow_log = time.monotonic()
            logger.warning("database_slow_operation", extra={"duration_ms": round(elapsed * 1000)})
        scope.__exit__(type(error) if error is not None else None, error, None)

    def after(
        _connection: Connection,
        _cursor: DBAPICursor,
        _statement: str,
        _parameters: object,
        context: ExecutionContext,
        _many: bool,
    ) -> None:
        finish(context)

    def failed(context: ExceptionContext) -> None:
        if context.execution_context is not None:
            finish(context.execution_context, context.original_exception)

    def checkout(
        _connection: object, record: ConnectionPoolEntry, _proxy: PoolProxiedConnection
    ) -> None:
        if runtime := current():
            record.info["devfeed_metrics_pid"] = os.getpid()
            if instrument := runtime.instruments.get("db_connections"):
                instrument.add(1, {"state": "used"})

    def checkin(_connection: object, record: ConnectionPoolEntry) -> None:
        if (
            record.info.pop("devfeed_metrics_pid", None) == os.getpid()
            and (runtime := current())
            and (instrument := runtime.instruments.get("db_connections"))
        ):
            instrument.add(-1, {"state": "used"})

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
