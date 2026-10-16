"""Persist verified provider identities and authenticated user activity."""

import logging
from datetime import timedelta

from devfeed_core.db import session_factory
from devfeed_core.models import UserAccount, utcnow
from devfeed_core.reader_acquisition import record_verified_signup
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def save_user(identity: dict) -> str:
    # Generic account saves never grant the callback-only signup marker.
    return save_user_with_status(
        {key: value for key, value in identity.items() if key != "_reader_signup"}
    )[0]


def save_user_with_status(identity: dict) -> tuple[str, bool]:
    """Return the committed account ID and whether this request created it."""
    values = {
        name: identity[name] for name in ("issuer", "subject", "organization_id", "name", "email")
    }
    now = utcnow()
    values["last_seen_at"] = now
    capture = identity.get("_reader_signup")
    if isinstance(capture, dict):
        values["created_at"] = values["reader_signup_at"] = now
    with session_factory().begin() as session:
        # The unique constraint arbitrates concurrent callbacks; only the winning
        # insert represents a sign-up. The account must commit before sending it.
        account_id = session.scalar(
            insert(UserAccount)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_user_identity")
            .returning(UserAccount.id)
        )
        created = account_id is not None
        if created and isinstance(capture, dict):
            try:
                # Preserve the authoritative account marker even if optional
                # attribution/outbox persistence is unavailable.
                with session.begin_nested():
                    record_verified_signup(session, account_id, now, capture)
            except Exception as exc:
                logger.warning(
                    "reader_signup_measurement_unavailable",
                    extra={"error_type": type(exc).__name__},
                )
        if not created:
            account_id = session.scalar(
                update(UserAccount)
                .where(
                    UserAccount.issuer == identity["issuer"],
                    UserAccount.subject == identity["subject"],
                )
                .values(
                    **{
                        name: value
                        for name, value in values.items()
                        if name not in {"issuer", "subject"}
                        and name not in {"created_at", "reader_signup_at"}
                    }
                )
                .returning(UserAccount.id)
            )
            if account_id is None:
                raise SQLAlchemyError("User account disappeared during sign-in")
    return str(account_id), created


def touch_user_activity(user_id: str) -> None:
    """Persist authenticated platform activity at most once every 15 minutes."""
    now = utcnow()
    try:
        with session_factory().begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            session.execute(
                update(UserAccount)
                .where(
                    UserAccount.id == user_id,
                    UserAccount.last_seen_at < now - timedelta(minutes=15),
                )
                .values(last_seen_at=now)
            )
    except SQLAlchemyError:
        # Activity telemetry must never make an otherwise valid request fail.
        logger.warning("user_activity_update_deferred", extra={"user_id": user_id})
