"""RQ entry point for durable per-user recommendation refreshes."""

from devfeed_core.db import session_factory
from devfeed_core.recommendations import refresh_recommendations


def refresh(user_id: str) -> int:
    return refresh_recommendations(session_factory(), user_id)
