"""Persist verified provider identities, never linking users by email."""

from devfeed_core.db import session_factory
from devfeed_core.models import UserAccount, utcnow
from sqlalchemy.dialects.postgresql import insert


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
