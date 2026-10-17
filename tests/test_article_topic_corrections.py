"""Reviewed topic corrections preserve unrelated classifications and audit changes."""

import runpy
import uuid
from pathlib import Path

import pytest
from devfeed_core import article_topic_corrections as corrections
from devfeed_core import cache_events
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleOrigin,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    RecommendationTopicEvent,
    SearchEvent,
    Source,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.publication_policy import evaluate_publication
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import fingerprint
from pydantic import ValidationError
from sqlalchemy import delete, select, text

EVIDENCE = (
    "At 8th Light, our approach is anchored in the three pillars of human-centered "
    "design: viability, feasibility, and desirability."
)
SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/repair_article_topic_matches.py")
)


def intent(article_id, remove_id, replacement_id=None, *, restore_publication=False):
    return corrections.TopicCorrectionIntent(
        article_id=article_id,
        remove_topic_ids=[remove_id],
        topics=[
            {
                "topic_id": replacement_id,
                "role": "primary",
                "relevance": 0.95,
                "evidence": EVIDENCE,
            }
        ]
        if replacement_id
        else [],
        actor="reviewing-operator",
        note="Iteration describes design iteration here, not a programming loop.",
        restore_publication=restore_publication,
    )


@pytest.mark.parametrize("field", ["remove_topic_ids", "topics"])
def test_correction_rejects_duplicate_selections(field):
    body = intent(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()).model_dump()
    body[field] *= 2
    with pytest.raises(ValidationError, match="only once"):
        corrections.TopicCorrectionIntent.model_validate(body)


@pytest.mark.parametrize("count", [0, 26])
def test_script_requires_bounded_explicit_article_ids(count):
    with pytest.raises(ValueError, match="between 1 and 25"):
        SCRIPT["repair"]({"corrections": [{}] * count})


def test_script_apply_requires_saved_revision_and_fingerprint():
    body = intent(uuid.uuid4(), uuid.uuid4()).model_dump(mode="json")
    with pytest.raises(ValidationError, match="expected_revision"):
        SCRIPT["repair"]({"corrections": [body]}, apply=True)


def test_script_rejects_repeated_article_id_before_opening_transaction():
    body = intent(uuid.uuid4(), uuid.uuid4()).model_dump(mode="json")
    with pytest.raises(ValueError, match="once per batch"):
        SCRIPT["repair"]({"corrections": [body, body]})


@pytest.fixture
def correction_data(database):
    with database.begin() as session:
        source = Source(
            name="Design publisher",
            feed_url="https://publisher.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        original = Topic(
            name="Iteration",
            slug="iteration",
            kind="concept",
            status="active",
            description="Programming loops and repeated computation.",
        )
        user = Topic(name="Users", slug="users", kind="concept", status="active")
        testing = Topic(
            name="Software Testing", slug="software-testing", kind="practice", status="active"
        )
        design = Topic(
            name="Human centric design",
            slug="human-centric-design",
            kind="concept",
            status="active",
            description="Design with people and end users at the center.",
        )
        tag = Tag(name="Design", slug="design")
        article = Article(
            canonical_url="https://publisher.example/human-centered-design",
            url_hash=fingerprint("https://publisher.example/human-centered-design"),
            title="Human-Centered Design: Clarity",
            summary=EVIDENCE,
            ai_title="How human-centered design clarifies decisions",
            ai_summary="Teams test design ideas against users' needs.",
            ai_description="A design workflow built around people.",
            language="en",
            content_type="article",
            content_format="article",
            editorial_revision=4,
            review_status="approved",
            publication_status="published",
            classification_provenance={
                "origin": "ai",
                "analysis_id": str(uuid.uuid4()),
                "developer_relevance": "relevant",
                "page_kind": "article",
            },
            published_to_feed_at=utcnow(),
        )
        session.add_all([source, original, user, testing, design, tag, article])
        session.flush()
        session.add_all(
            [
                ArticleTopic(
                    article_id=article.id,
                    topic_id=original.id,
                    role="primary",
                    relevance=0.95,
                    evidence="iteration",
                    origin="ai",
                ),
                ArticleTopic(
                    article_id=article.id,
                    topic_id=user.id,
                    role="supporting",
                    relevance=0.7,
                    evidence="users",
                    origin="ai",
                ),
                ArticleTopic(
                    article_id=article.id,
                    topic_id=testing.id,
                    role="supporting",
                    relevance=0.86,
                    evidence="test design ideas",
                    origin="manual",
                ),
                ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"),
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key="design",
                    original_url=article.canonical_url,
                ),
                ArticleContent(
                    article_id=article.id,
                    url=article.canonical_url,
                    text=EVIDENCE,
                    content_hash=fingerprint(EVIDENCE),
                    method="body",
                ),
            ]
        )
        return {
            "article": article.id,
            "original": original.id,
            "user": user.id,
            "testing": testing.id,
            "design": design.id,
            "tag": tag.id,
        }


def plan(database, data, *, replacement=True, restore=False):
    body = intent(
        data["article"],
        data["original"],
        data["design"] if replacement else None,
        restore_publication=restore,
    )
    with database() as session:
        preview = corrections.preview_article_topic_correction(session, body)
        return corrections.ArticleTopicCorrection.model_validate(preview["correction"])


@pytest.mark.integration
def test_preview_is_read_only_and_preserves_publication(database, correction_data):
    data = correction_data
    with database.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        preview = corrections.preview_article_topic_correction(
            session, intent(data["article"], data["original"], data["design"])
        )
        assert not session.new and not session.dirty and not session.deleted
        assert preview["removed"][0]["topic_id"] == str(data["original"])
        assert preview["added"][0]["topic_id"] == str(data["design"])
        assert preview["publication_status"] == "unpublished"
    with database() as session:
        article = session.get(Article, data["article"])
        assert (article.editorial_revision, article.publication_status) == (4, "published")
        assert session.scalars(select(ArticleReview)).all() == []


@pytest.mark.integration
def test_apply_preserves_manual_and_unrelated_assignments_tags_and_prose(database, correction_data):
    data = correction_data
    body = plan(database, data)
    with database.begin() as session:
        result = corrections.apply_article_topic_correction(session, body)
        assert result["status"] == "applied" and result["editorial_revision"] == 5
    with database() as session:
        article = session.get(Article, data["article"])
        assert (article.review_status, article.publication_status) == ("pending", "unpublished")
        assert article.summary == EVIDENCE
        assert article.ai_title == "How human-centered design clarifies decisions"
        assert article.ai_summary == "Teams test design ideas against users' needs."
        assert article.ai_description == "A design workflow built around people."
        assert (article.language, article.content_type, article.content_format) == (
            "en",
            "article",
            "article",
        )
        assert article.classification_provenance["origin"] == "manual"
        assert session.get(ArticleTopic, (article.id, data["original"])) is None
        retained = session.get(ArticleTopic, (article.id, data["testing"]))
        assert (retained.origin, retained.role, retained.relevance, retained.evidence) == (
            "manual",
            "supporting",
            0.86,
            "test design ideas",
        )
        assert session.get(ArticleTopic, (article.id, data["user"])).origin == "ai"
        assert session.get(ArticleTag, (article.id, data["tag"])).origin == "source"
        added = session.get(ArticleTopic, (article.id, data["design"]))
        assert (added.origin, added.role, added.evidence) == ("manual", "primary", EVIDENCE)
        review = session.scalar(
            select(ArticleReview).where(ArticleReview.action == corrections.ACTION)
        )
        assert review.actor == body.actor and review.note == body.note and review.revision == 5
        assert review.automation["removed"][0]["topic_id"] == str(data["original"])
        assert review.automation["added"][0]["topic_id"] == str(data["design"])
        assert review.automation["previous_provenance"]["origin"] == "ai"


@pytest.mark.integration
@pytest.mark.parametrize("restore", [False, True])
def test_apply_is_idempotent_after_final_editorial_state(database, correction_data, restore):
    data = correction_data
    body = plan(database, data, restore=restore)
    with database.begin() as session:
        publication_date = session.get(Article, data["article"]).published_to_feed_at
        corrections.apply_article_topic_correction(session, body)
    with database.begin() as session:
        assert (
            corrections.apply_article_topic_correction(session, body)["status"] == "already_applied"
        )
    with database() as session:
        article = session.get(Article, data["article"])
        assert article.editorial_revision == (7 if restore else 5)
        assert article.publication_status == ("published" if restore else "unpublished")
        assert article.review_status == ("approved" if restore else "pending")
        assert article.published_to_feed_at == publication_date
        reviews = session.scalars(select(ArticleReview)).all()
        assert sorted(review.action for review in reviews) == (
            ["approve", "classify", "correct_topics", "publish"]
            if restore
            else ["classify", "correct_topics"]
        )


@pytest.mark.integration
def test_correction_keeps_existing_human_review_publication_requirement(database, correction_data):
    data = correction_data
    body = plan(database, data)
    with database.begin() as session:
        corrections.apply_article_topic_correction(session, body)
    with database() as session:
        article = session.get(Article, data["article"])
        review = session.scalar(
            select(ArticleReview).where(
                ArticleReview.action == "classify", ArticleReview.automation == {}
            )
        )
        assert review.actor == body.actor and review.revision == 5
        assert "human_review_required" in evaluate_publication(session, article, None)["reasons"]


@pytest.mark.integration
@pytest.mark.parametrize("change", ["revision", "evidence", "origin", "source", "tag", "meaning"])
def test_apply_rejects_changes_since_review(database, correction_data, change):
    data = correction_data
    body = plan(database, data)
    with database.begin() as session:
        if change == "revision":
            session.get(Article, data["article"]).editorial_revision += 1
        elif change in {"evidence", "origin"}:
            setattr(
                session.get(ArticleTopic, (data["article"], data["original"])),
                change,
                "changed" if change == "evidence" else "manual",
            )
        elif change == "source":
            session.get(ArticleContent, data["article"]).text += " New extraction."
        elif change == "tag":
            session.get(ArticleTag, (data["article"], data["tag"])).origin = "manual"
        else:
            session.get(Topic, data["design"]).description = "A different semantic identity."
    with pytest.raises(OperationConflict, match="changed"), database.begin() as session:
        corrections.apply_article_topic_correction(session, body)
    with database() as session:
        assert session.get(ArticleTopic, (data["article"], data["original"])) is not None
        assert session.scalars(select(ArticleReview)).all() == []


@pytest.mark.integration
def test_manual_assignment_cannot_be_removed_or_overwritten(database, correction_data):
    data = correction_data
    with database() as session:
        with pytest.raises(OperationConflict, match="Only inferred"):
            corrections.preview_article_topic_correction(
                session, intent(data["article"], data["testing"])
            )
        with pytest.raises(OperationConflict, match="retained topic"):
            corrections.preview_article_topic_correction(
                session, intent(data["article"], data["original"], data["testing"])
            )


@pytest.mark.integration
@pytest.mark.parametrize("invalid", ["inactive", "evidence"])
def test_replacement_requires_active_topic_and_actual_source_evidence(
    database, correction_data, invalid
):
    data = correction_data
    body = intent(data["article"], data["original"], data["design"])
    if invalid == "inactive":
        with database.begin() as session:
            session.get(Topic, data["design"]).status = "proposed"
    else:
        body.topics[0].evidence = "Invented quotation absent from the article."
    with (
        database() as session,
        pytest.raises((OperationConflict, ValueError), match="active|evidence"),
    ):
        corrections.preview_article_topic_correction(session, body)


@pytest.mark.integration
def test_removing_only_primary_without_restore_unpublishes_for_review(database, correction_data):
    data = correction_data
    body = plan(database, data, replacement=False)
    with database.begin() as session:
        corrections.apply_article_topic_correction(session, body)
    with database() as session:
        article = session.get(Article, data["article"])
        assert article.publication_status == "unpublished" and article.review_status == "pending"
        assert not any(link.role == "primary" for link in article.topic_links)


@pytest.mark.integration
def test_restore_uses_existing_publication_gates_and_rolls_back(database, correction_data):
    data = correction_data
    body = plan(database, data, replacement=False, restore=True)
    with (
        pytest.raises(OperationConflict, match="missing_active_primary_topic"),
        database.begin() as session,
    ):
        corrections.apply_article_topic_correction(session, body)
    with database() as session:
        article = session.get(Article, data["article"])
        assert (article.editorial_revision, article.publication_status) == (4, "published")
        assert session.get(ArticleTopic, (article.id, data["original"])) is not None
        assert session.scalars(select(ArticleReview)).all() == []


@pytest.mark.integration
def test_restore_does_not_publish_previously_unpublished_article(database, correction_data):
    data = correction_data
    with database.begin() as session:
        session.get(Article, data["article"]).publication_status = "unpublished"
    with database() as session, pytest.raises(OperationConflict, match="previously published"):
        corrections.preview_article_topic_correction(
            session,
            intent(data["article"], data["original"], data["design"], restore_publication=True),
        )


@pytest.mark.integration
def test_repair_refreshes_public_cache_search_and_recommendation_events(
    database, correction_data, monkeypatch
):
    data = correction_data
    body = plan(database, data)
    with database.begin() as session:
        session.execute(delete(SearchEvent))
        before = dict(
            session.execute(
                select(RecommendationTopicEvent.topic_id, RecommendationTopicEvent.version)
            ).all()
        )
    invalidations = []
    monkeypatch.setattr(
        cache_events, "invalidate_public_cache", lambda **kw: invalidations.append(kw)
    )
    with database.begin() as session:
        corrections.apply_article_topic_correction(session, body)
        assert invalidations == []
    assert len(invalidations) == 1
    assert "article_topics" in invalidations[0]["reasons"]
    with database() as session:
        events = {(row.kind, row.entity_id) for row in session.scalars(select(SearchEvent))}
        assert ("articles", data["article"]) in events
        assert ("topics", data["original"]) in events
        assert ("topics", data["design"]) in events
        after = dict(
            session.execute(
                select(RecommendationTopicEvent.topic_id, RecommendationTopicEvent.version)
            ).all()
        )
        assert after[data["original"]] > before[data["original"]]
        assert after[data["design"]] > before.get(data["design"], 0)


@pytest.mark.integration
def test_script_preview_apply_replay_and_batch_rollback(database, correction_data):
    data = correction_data
    document = {
        "corrections": [
            intent(data["article"], data["original"], data["design"]).model_dump(mode="json")
        ]
    }
    preview = SCRIPT["repair"](document)
    missing = intent(uuid.UUID(int=2**128 - 1), uuid.uuid4()).model_dump(mode="json")
    missing.update(expected_revision=0, expected_state_hash="0" * 64)
    with pytest.raises(RecordNotFound):
        SCRIPT["repair"]({"corrections": [preview["corrections"][0], missing]}, apply=True)
    with database() as session:
        assert session.get(Article, data["article"]).editorial_revision == 4
    assert SCRIPT["repair"](preview, apply=True)["changes"][0]["status"] == "applied"
    assert SCRIPT["repair"](preview, apply=True)["changes"][0]["status"] == "already_applied"
