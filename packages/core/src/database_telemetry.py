"""SQLAlchemy instrumentation without SQL text, bind values, or row contents."""

import logging
import os
import time

from sqlalchemy import event
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool

from devfeed_core.telemetry import current, span

logger = logging.getLogger(__name__)


class ObservedQueuePool(QueuePool):
    # Keep SQLAlchemy lifecycle/debug messages under its existing log policy.
    _sqla_logger_namespace = "sqlalchemy.pool"

    def connect(self):
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
                runtime.metrics.pool_acquisition.labels(runtime.service, result).observe(
                    time.monotonic() - started
                )


def operation(statement: str) -> str:
    word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
    return word.lower() if word in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "other"


def instrument_engine(engine) -> None:
    last_slow_log = 0.0

    def before(_connection, _cursor, statement, _parameters, context, _many):
        if not current():
            return
        verb = operation(statement)
        scope = span(
            "postgresql " + verb,
            attributes={"db.system.name": "postgresql", "db.operation.name": verb},
        )
        active = scope.__enter__()
        context._devfeed_query = (time.monotonic(), verb, scope, active)

    def finish(context, error=None):
        nonlocal last_slow_log
        value = getattr(context, "_devfeed_query", None)
        if not value:
            return
        del context._devfeed_query
        started, verb, scope, _active = value
        elapsed = time.monotonic() - started
        if runtime := current():
            runtime.metrics.db_duration.labels(runtime.service, verb).observe(elapsed)
            if error is not None:
                runtime.metrics.db_errors.labels(runtime.service, verb).inc()
        # Bounded diagnostic signal with no SQL, parameters or model data.
        if elapsed >= 1 and time.monotonic() - last_slow_log >= 30:
            last_slow_log = time.monotonic()
            logger.warning("database_slow_operation", extra={"duration_ms": round(elapsed * 1000)})
        scope.__exit__(type(error) if error is not None else None, error, None)

    def after(_connection, _cursor, _statement, _parameters, context, _many):
        finish(context)

    def failed(context):
        if context.execution_context is not None:
            finish(context.execution_context, context.original_exception)
        elif runtime := current():
            runtime.metrics.db_errors.labels(runtime.service, "connection").inc()

    def checkout(_connection, record, _proxy):
        if runtime := current():
            record.info["devfeed_metrics_pid"] = os.getpid()
            record.info["devfeed_checkout_at"] = time.monotonic()
            runtime.metrics.pool_connections.labels(runtime.service).inc()

    def checkin(_connection, record):
        if record.info.pop("devfeed_metrics_pid", None) == os.getpid() and (runtime := current()):
            runtime.metrics.pool_connections.labels(runtime.service).dec()
            started = record.info.pop("devfeed_checkout_at", None)
            if started is not None:
                runtime.metrics.pool_hold.labels(runtime.service).observe(
                    time.monotonic() - started
                )

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
