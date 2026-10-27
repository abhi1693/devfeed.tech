"""Retry only short, database-only transactions, never external side effects."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


def run_transaction[R](
    factory: sessionmaker[Session],
    operation: Callable[[Session], R],
    *,
    attempts: int = 3,
) -> R | None:
    for attempt in range(attempts):
        try:
            with factory.begin() as session:
                return operation(session)
        except DBAPIError as error:
            if (
                getattr(error.orig, "sqlstate", None) not in {"40P01", "40001"}
                or attempt + 1 == attempts
            ):
                raise
            time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))
    return None
