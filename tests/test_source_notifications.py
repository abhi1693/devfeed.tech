"""Source decisions and private receipts share one durable transaction."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core import services
from devfeed_core.config import get_settings
from devfeed_core.models import NotificationDelivery, Source, SourceReview, UserAccount
from devfeed_core.notifications import notification_subscriber_id
from devfeed_core.schemas import SourceDecision
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.fixture
def suggestion(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    monkeypatch.setattr(get_settings(), "chimely_user_environment", "users")
    user_id, source_id = uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        session.add_all(
            [
                UserAccount(
                    id=user_id, issuer="https://id.test", subject="submitter", organization_id="org"
                ),
                UserAccount(
                    issuer="https://id.test", subject="someone-else", organization_id="org"
                ),
                Source(
                    id=source_id,
                    name="Example Engineering",
                    feed_url="https://example.com/feed",
                    source_type="publisher",
                    approval_status="pending",
                    submitted_by={"user_id": str(user_id), "verified": True, "name": "Submitter"},
                ),
            ]
        )
    return database, source_id, user_id


def receipts(session):
    return session.scalars(
        select(NotificationDelivery).where(NotificationDelivery.category.like("sources.%"))
    ).all()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_decision_notifies_only_submitter_once(suggestion, decision):
    factory, source_id, _ = suggestion
    body = SourceDecision(
        decision=decision, actor="Private admin identity", note="Not developer content"
    )
    with factory.begin() as session:
        services.review_source(session, source_id, body)
        services.review_source(session, source_id, body)
    with factory() as session:
        (delivery,) = receipts(session)
        review = session.scalar(select(SourceReview))
        assert delivery.event_key == f"source-review:{review.id}"
        assert delivery.audience == "user"
        assert delivery.subscriber_id == notification_subscriber_id(
            audience="user", issuer="https://id.test", subject="submitter", organization_id="org"
        )
        assert delivery.category == f"sources.{decision}"
        assert "Example Engineering" in delivery.payload["body"]
        assert "Private admin identity" not in str(delivery.payload)
        if decision == "approved":
            assert delivery.payload["action_url"] == f"/sources/{source_id}"
            assert delivery.payload["severity"] == "success"
            assert body.note not in delivery.payload["body"]
        else:
            assert delivery.payload["action_url"] == "/sources/suggest"
            assert body.note in delivery.payload["body"]


def test_rollback_cannot_leave_a_notification(suggestion):
    factory, source_id, _ = suggestion
    with factory() as session:
        services.review_source(
            session, source_id, SourceDecision(decision="rejected", note="Wrong feed")
        )
        assert len(receipts(session)) == 1
        session.rollback()
    with factory() as session:
        assert session.get(Source, source_id).approval_status == "pending"
        assert not receipts(session)
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0


def test_reversing_a_decision_creates_a_new_receipt(suggestion):
    factory, source_id, _ = suggestion
    for decision in ["approved", "rejected", "approved"]:
        with factory.begin() as session:
            services.review_source(
                session, source_id, SourceDecision(decision=decision, note="Review updated")
            )
    with factory() as session:
        deliveries = receipts(session)
        assert len(deliveries) == len({d.event_key for d in deliveries}) == 3


def test_concurrent_approvals_do_not_duplicate_receipts(suggestion):
    factory, source_id, _ = suggestion

    def approve(_):
        with factory.begin() as session:
            services.review_source(session, source_id, SourceDecision(decision="approved"))

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(approve, range(3)))
    with factory() as session:
        assert len(receipts(session)) == 1


@pytest.mark.parametrize(
    "case",
    ["disabled", "no_environment", "unattributed", "unverified", "invalid_id", "deleted_user"],
)
def test_ineligible_submissions_never_broadcast(suggestion, monkeypatch, case):
    factory, source_id, user_id = suggestion
    if case == "disabled":
        monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    if case == "no_environment":
        monkeypatch.setattr(get_settings(), "chimely_user_environment", None)
    with factory.begin() as session:
        source = session.get(Source, source_id)
        if case == "unattributed":
            source.submitted_by = None
        if case == "unverified":
            source.submitted_by = {"user_id": str(user_id), "verified": False}
        if case == "invalid_id":
            source.submitted_by = {"user_id": "invalid", "verified": True}
        if case == "deleted_user":
            session.delete(session.get(UserAccount, user_id))
        services.review_source(
            session, source_id, SourceDecision(decision="rejected", note="Wrong feed")
        )
    with factory() as session:
        assert session.get(Source, source_id).approval_status == "rejected"
        assert not receipts(session)


def test_long_rejection_reason_does_not_break_review(suggestion):
    factory, source_id, _ = suggestion
    with factory.begin() as session:
        services.review_source(
            session, source_id, SourceDecision(decision="rejected", note="x" * 1000)
        )
    with factory() as session:
        (delivery,) = receipts(session)
        assert len(delivery.payload["body"]) == 1000
        assert delivery.payload["body"].endswith("…")


def test_automatic_relevance_approval_notifies_submitter(suggestion, monkeypatch):
    from devfeed_aggregator import source_tasks
    from devfeed_core.source_enrichment import request_enrichment

    factory, source_id, _ = suggestion
    monkeypatch.setattr(get_settings(), "full_automation", True)
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(
        source_tasks,
        "assess_source",
        lambda *_: {"approval_supported": True, "reason": "Engineering feed"},
    )
    with factory.begin() as session:
        job_id = request_enrichment(session, source_id).id
    source_tasks.enrich_source(str(job_id))
    with factory() as session:
        assert session.get(Source, source_id).approval_status == "approved"
        (delivery,) = receipts(session)
        assert delivery.category == "sources.approved" and delivery.subscriber_id.startswith(
            "user_"
        )
        assert session.scalar(select(SourceReview.actor)) == "devfeed:source-relevance"
