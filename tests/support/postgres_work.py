"""SQL work and transaction lifetime observations on explicitly disposable PostgreSQL."""

import os
import re
import time
from collections import Counter
from contextlib import contextmanager

import psycopg
from sqlalchemy import event


@contextmanager
def postgres_work(engine):
    counts = Counter(
        sql_calls=0, sql_rows=0, transactions=0, active_connections=0, peak_connections=0
    )
    statements = Counter()
    query_started = []
    transaction_started = {}
    durations = []
    server = None
    if os.environ.get("DEVFEED_TEST_POSTGRES_STATS") == "1":
        # The integration fixture already enforces the explicit *_test URL.
        assert (engine.url.database or "").endswith("_test")
        url = engine.url.render_as_string(hide_password=False).replace(
            "postgresql+psycopg:", "postgresql:"
        )
        server = psycopg.connect(url, autocommit=True)
        server.execute("/* pg-work observer */ CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        server.execute("/* pg-work observer */ SELECT pg_stat_statements_reset()")

    def before(connection, cursor, statement, parameters, context, executemany):
        query_started.append(time.perf_counter())
        counts["sql_calls"] += len(parameters) if executemany else 1
        table = re.search(r"\b(?:FROM|UPDATE|INTO)\s+([^\s(]+)", statement, re.I)
        key = statement.split()[0] + " " + (table[1] if table else "control")
        statements[key] += len(parameters) if executemany else 1

    def after(connection, cursor, statement, parameters, context, executemany):
        counts["client_sql_seconds"] += time.perf_counter() - query_started.pop()
        counts["sql_rows"] += max(0, cursor.rowcount)

    def checkout(*args):
        counts["active_connections"] += 1
        counts["peak_connections"] = max(counts["peak_connections"], counts["active_connections"])

    def checkin(*args):
        counts["active_connections"] -= 1

    def begin(connection):
        counts["transactions"] += 1
        transaction_started[id(connection)] = time.perf_counter()

    def end(connection):
        if (started := transaction_started.pop(id(connection), None)) is not None:
            durations.append(time.perf_counter() - started)

    listeners = [
        ("before_cursor_execute", before),
        ("after_cursor_execute", after),
        ("checkout", checkout),
        ("checkin", checkin),
        ("begin", begin),
        ("commit", end),
        ("rollback", end),
    ]
    for name, callback in listeners:
        event.listen(engine, name, callback)
    try:
        yield counts
    finally:
        for name, callback in listeners:
            event.remove(engine, name, callback)
        counts["max_transaction_seconds"] = max(durations, default=0)
        counts["statements"] = dict(statements)
        if server is not None:
            columns = [
                "calls",
                "rows",
                "total_exec_time",
                "total_plan_time",
                "shared_blks_hit",
                "shared_blks_read",
                "shared_blks_dirtied",
                "shared_blks_written",
                "wal_bytes",
            ]
            row = server.execute(
                "/* pg-work observer */ SELECT "
                + ",".join(f"coalesce(sum({column}),0)" for column in columns)
                + " FROM pg_stat_statements WHERE dbid="
                "(SELECT oid FROM pg_database WHERE datname=current_database()) "
                "AND query NOT LIKE '/* pg-work observer */%'"
            ).fetchone()
            counts["server"] = dict(zip(columns, map(float, row), strict=True))
            updates = server.execute(
                "/* pg-work observer */ SELECT coalesce(sum(calls),0),coalesce(sum(rows),0) "
                "FROM pg_stat_statements WHERE dbid="
                "(SELECT oid FROM pg_database WHERE datname=current_database()) "
                "AND query ~* '^UPDATE .*notification_deliveries'"
            ).fetchone()
            (
                counts["server"]["notification_update_calls"],
                counts["server"]["notification_update_rows"],
            ) = map(float, updates)
            server.close()
