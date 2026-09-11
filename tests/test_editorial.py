import uuid
from types import SimpleNamespace

import pytest
from devfeed_core import editorial
from devfeed_core.models import Article, ArticleReview, ArticleTopic, Topic, utcnow
from devfeed_core.schemas import ArticleOut
from devfeed_core.services import OperationConflict


def article(**overrides):
    topic = Topic(
        id=uuid.uuid4(), name="Angular", slug="angular", kind="framework", status="active"
    )
    values = dict(
        id=uuid.uuid4(),
        slug="example-article-1",
        title="JavaScript and Angular routing",
        canonical_url="https://example.com/angular",
        summary="",
        ai_summary=(
            "This article explains Angular routing and how developers build "
            "navigation in JavaScript applications."
        ),
        language="en",
        content_type="tutorial",
        content_format="article",
        review_status="pending",
        publication_status="unpublished",
        editorial_revision=0,
        classification_provenance={"developer_relevance": "relevant"},
        discovered_at=utcnow(),
        feed_at=utcnow(),
        tags=[],
        origins=[],
        topic_links=[
            ArticleTopic(
                topic=topic,
                topic_id=topic.id,
                role="primary",
                relevance=0.9,
                evidence="Angular routing",
            )
        ],
    )
    return Article(**{**values, **overrides})


def session(current):
    added = []
    return SimpleNamespace(scalar=lambda _: current, add=added.append, added=added)


@pytest.fixture(autouse=True)
def approved_source(monkeypatch):
    monkeypatch.setattr(editorial, "approved_sources", lambda *_: [uuid.uuid4()])


def test_approval_does_not_publish_and_generated_prose_remains_distinct():
    current = article()
    db = session(current)
    editorial.decide_article(db, current.id, editorial.EditorialDecision(action="approve"))
    assert current.review_status == "approved" and current.publication_status == "unpublished"
    assert current.summary == "" and current.ai_summary
    assert current.editorial_revision == 1
    assert isinstance(db.added[0], ArticleReview)
    assert editorial.publication_blockers(current) == []
    editorial.decide_article(db, current.id, editorial.EditorialDecision(action="publish"))
    assert current.publication_status == "published" and current.published_to_feed_at
    assert current.published_at is None


@pytest.mark.parametrize(
    "values,reason",
    [
        ({"ai_summary": None}, "missing_summary"),
        ({"ai_summary": "short"}, "missing_summary"),
        ({"language": None}, "unknown_language"),
        ({"language": "und"}, "unknown_language"),
        ({"content_type": None}, "unknown_content_type"),
        ({"content_format": None}, "unknown_content_format"),
        ({"topic_links": []}, "missing_active_primary_topic"),
        ({"classification_provenance": {}}, "developer_relevance_unresolved"),
        ({"review_status": "pending"}, "not_approved"),
        ({"canonical_url": "http://127.0.0.1"}, "invalid_canonical_url"),
    ],
)
def test_publication_fails_closed_with_specific_reasons(values, reason):
    current = article(**{"review_status": "approved", **values})
    with pytest.raises(OperationConflict, match=reason):
        editorial.decide_article(
            session(current), current.id, editorial.EditorialDecision(action="publish")
        )
    assert current.publication_status == "unpublished"


def test_supporting_or_proposed_topic_does_not_satisfy_primary_requirement():
    current = article(review_status="approved")
    current.topic_links[0].role = "supporting"
    assert "missing_active_primary_topic" in editorial.publication_blockers(current)
    current.topic_links[0].role = "primary"
    current.topic_links[0].topic.status = "proposed"
    assert "missing_active_primary_topic" in editorial.publication_blockers(current)


def test_reject_requires_reason_and_unpublishes_with_revision():
    current = article(review_status="approved", publication_status="published")
    with pytest.raises(OperationConflict, match="reason"):
        editorial.decide_article(
            session(current), current.id, editorial.EditorialDecision(action="reject")
        )
    editorial.decide_article(
        session(current), current.id, editorial.EditorialDecision(action="reject", note="Unrelated")
    )
    assert current.review_status == "rejected" and current.publication_status == "unpublished"
    assert current.editorial_revision == 1
    editorial.invalidate_editorial(current)
    assert current.review_status == "rejected"


def test_unpublish_preserves_approval_and_republish_preserves_first_publication_date():
    published = utcnow()
    current = article(
        review_status="approved", publication_status="published", published_to_feed_at=published
    )
    editorial.decide_article(
        session(current), current.id, editorial.EditorialDecision(action="unpublish")
    )
    assert current.review_status == "approved" and current.publication_status == "unpublished"
    editorial.decide_article(
        session(current), current.id, editorial.EditorialDecision(action="publish")
    )
    assert current.published_to_feed_at == published


def test_dry_run_and_stale_revision_do_not_mutate():
    current = article()
    db = session(current)
    editorial.decide_article(
        db, current.id, editorial.EditorialDecision(action="approve"), dry_run=True
    )
    assert current.review_status == "pending" and not db.added
    with pytest.raises(OperationConflict, match="changed"):
        editorial.decide_article(
            db, current.id, editorial.EditorialDecision(action="approve", expected_revision=1)
        )


def test_public_payload_exposes_ai_prose_not_private_evidence_or_review_state():
    payload = ArticleOut.from_article(article()).model_dump()
    assert payload["summary"] == "" and payload["ai_summary"]
    assert payload["topics"][0]["slug"] == "angular"
    assert (
        not {"classification_provenance", "review_status", "input_snapshot", "editorial_revision"}
        & payload.keys()
    )


def test_no_approved_source_blocks_publish(monkeypatch):
    monkeypatch.setattr(editorial, "approved_sources", lambda *_: [])
    current = article(review_status="approved")
    with pytest.raises(OperationConflict, match="no_approved_source"):
        editorial.decide_article(
            session(current), current.id, editorial.EditorialDecision(action="publish")
        )
