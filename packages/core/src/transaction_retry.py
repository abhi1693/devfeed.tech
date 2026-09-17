"""Retry only short, database-only transactions, never external side effects."""

import random
import time

from sqlalchemy.exc import DBAPIError


def run_transaction(factory, operation, *, attempts=3):
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
