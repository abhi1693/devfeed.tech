"""SQLAlchemy instrumentation without SQL text, bind values, or row contents."""

import os
import time

from sqlalchemy import event

from devfeed_core.telemetry import current, span


def operation(statement: str) -> str:
    word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
    return word.lower() if word in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "other"


def instrument_engine(engine) -> None:
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
        value = getattr(context, "_devfeed_query", None)
        if not value:
            return
        del context._devfeed_query
        started, verb, scope, _active = value
        if runtime := current():
            runtime.metrics.db_duration.labels(runtime.service, verb).observe(
                time.monotonic() - started
            )
            if error is not None:
                runtime.metrics.db_errors.labels(runtime.service, verb).inc()
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
            runtime.metrics.pool_connections.labels(runtime.service).inc()

    def checkin(_connection, record):
        if record.info.pop("devfeed_metrics_pid", None) == os.getpid() and (runtime := current()):
            runtime.metrics.pool_connections.labels(runtime.service).dec()

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
