"""Observe a real worker waiting for a PostgreSQL row lock in concurrency tests."""

import threading
import time

from sqlalchemy import event, text


class RowLockWait:
    def __init__(self, factory, *, thread_prefix, table):
        self.factory = factory
        self.thread_prefix = thread_prefix
        self.table = table.upper()
        self.started = threading.Event()
        self.pid = None
        with factory() as session:
            self.engine = session.get_bind()

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._observe)
        return self

    def __exit__(self, *_args):
        event.remove(self.engine, "before_cursor_execute", self._observe)

    def _observe(self, connection, cursor, statement, parameters, context, executemany):
        if (
            threading.current_thread().name.startswith(self.thread_prefix)
            and "FOR UPDATE" in statement.upper()
            and self.table in statement.upper()
        ):
            self.pid = connection.connection.driver_connection.info.backend_pid
            self.started.set()

    def assert_blocked(self, future):
        assert self.started.wait(timeout=2), "Worker did not reach its targeted claim"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            assert not future.done(), "Worker discarded a delivery before its publisher committed"
            with self.factory() as session:
                waiting = session.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                    {"pid": self.pid},
                )
            if waiting == "Lock":
                return
            time.sleep(0.01)
        raise AssertionError("Worker never waited for the publisher's row lock")
