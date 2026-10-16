"""Materialize authenticated OIDC identities for administration user selection."""

from sqlalchemy.dialects.postgresql import insert

from devfeed_core.db import session_factory
from devfeed_core.models import UserAccount, utcnow


def register_verified_user(identity: dict) -> str:
    """Upsert a verified identity without changing its profile or granting membership."""
    values = {
        field: identity.get(field)
        for field in ("issuer", "subject", "organization_id", "name", "email")
    }
    values["last_seen_at"] = utcnow()
    statement = insert(UserAccount).values(**values)
    upsert = statement.on_conflict_do_update(
        constraint="uq_user_identity",
        set_={
            field: getattr(statement.excluded, field)
            for field in ("organization_id", "name", "email", "last_seen_at")
        },
    ).returning(UserAccount.id)
    with session_factory().begin() as session:
        return str(session.scalar(upsert))
