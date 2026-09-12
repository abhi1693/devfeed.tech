"""Stable transport identities for one due attempt of a durable database job."""

import uuid
from datetime import UTC, datetime


def delivery_id(kind: str, identifier: uuid.UUID, due_at: datetime, attempt: int = 0) -> str:
    due = due_at.astimezone(UTC).isoformat()
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"devfeed/{kind}/{identifier}/{due}/{attempt}")
    return f"devfeed-{kind}-{token}"
