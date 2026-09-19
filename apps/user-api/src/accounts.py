"""Persist verified provider identities and authenticated user activity."""

import logging
from datetime import timedelta

from devfeed_core.db import session_factory
from devfeed_core.models import UserAccount, utcnow
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def save_user(identity: dict) -> str:
    values = {
        name: identity[name] for name in ("issuer", "subject", "organization_id", "name", "email")
    }
    values["last_seen_at"] = utcnow()
    statement = insert(UserAccount).values(**values)
    upsert = statement.on_conflict_do_update(
        constraint="uq_user_identity",
        set_={name: value for name, value in values.items() if name not in {"issuer", "subject"}},
    ).returning(UserAccount.id)
    with session_factory().begin() as session:
        return str(session.scalar(upsert))


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
