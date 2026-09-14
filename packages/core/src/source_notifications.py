"""Private source-review receipts, persisted with the decision transaction."""

import uuid

from sqlalchemy.orm import Session

from devfeed_core.config import get_settings
from devfeed_core.models import Source, SourceReview, UserAccount
from devfeed_core.notifications import (
    NotificationMessage,
    enqueue_notification,
    notification_subscriber_id,
)


def notify_source_review(session: Session, source: Source, review: SourceReview) -> None:
    settings = get_settings()
    if not settings.notifications_enabled or not settings.chimely_user_environment:
        return
    submitter = source.submitted_by
    if not isinstance(submitter, dict) or submitter.get("verified") is not True:
        return
    try:
        user_id = uuid.UUID(str(submitter.get("user_id", "")))
    except ValueError:
        return
    user = session.get(UserAccount, user_id)
    if user is None:
        return  # Deleted accounts and unattributed submissions must never broadcast.
    approved = review.decision == "approved"
    body = f'Your source suggestion "{source.name}" was {review.decision}.'
    if not approved and review.note:
        body += f"\n\nReason: {review.note}"
    enqueue_notification(
        session,
        f"source-review:{review.id}",
        NotificationMessage(
            category=f"sources.{review.decision}",
            title="Source approved" if approved else "Source rejected",
            body=body if len(body) <= 1000 else body[:999] + "…",
            action_url=f"/sources/{source.id}" if approved else "/sources/suggest",
            severity="success" if approved else "warning",
        ),
        audience="user",
        subscriber_id=notification_subscriber_id(
            audience="user",
            issuer=user.issuer,
            subject=user.subject,
            organization_id=user.organization_id,
        ),
    )
