"""Retry contended partner claims without retaining catalog or row locks."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

CLAIM_TIMEOUT_SECONDS = 5.0
CLAIM_RETRY_SECONDS = 0.05


class PartnerClaimTimeout(TimeoutError):
    """Leave the durable job available for retry when its claim stays contended."""


class _ClaimBusy(Exception):
    pass


@contextmanager
def claim_partner_job[T](
    factory: sessionmaker[Session], acquire: Callable[[Session], T]
) -> Iterator[tuple[Session, T]]:
    deadline = time.monotonic() + CLAIM_TIMEOUT_SECONDS
    while True:
        if time.monotonic() >= deadline:
            raise PartnerClaimTimeout("Partner job claim remained contended")
        yielded = False
        try:
            with factory.begin() as session:
                # Relation/DDL locks must also release the catalog before retrying.
                session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                # This is lock_catalog's canonical advisory key. Never wait for it.
                locked = session.execute(
                    text("SELECT pg_try_advisory_xact_lock(734819025)")
                ).scalar_one()
                if not locked:
                    raise _ClaimBusy
                claimed = acquire(session)
                if time.monotonic() >= deadline:
                    raise PartnerClaimTimeout("Partner job claim remained contended")
                yielded = True
                yield session, claimed
            return
        except _ClaimBusy:
            if yielded:
                raise
        except DBAPIError as exc:
            if yielded or getattr(exc.orig, "sqlstate", None) != "55P03":
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PartnerClaimTimeout("Partner job claim remained contended")
        # The failed transaction has already released every lock and connection.
        time.sleep(min(CLAIM_RETRY_SECONDS, remaining))
