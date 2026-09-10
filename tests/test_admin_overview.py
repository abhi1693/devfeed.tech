"""Dashboard metrics against PostgreSQL, including UTC and editorial boundaries."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api import overview
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleTopic,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
)
from sqlalchemy import text

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
START = datetime(2026, 9, 3, tzinfo=UTC)


def article(**values):
    identity = uuid.uuid4()
    return Article(
        id=identity,
        canonical_url=f"https://example.com/{identity}",
        url_hash=identity.hex,
        title="Article",
        discovered_at=NOW,
        **values,
    )


def topic(name, status="active"):
    return Topic(id=uuid.uuid4(), name=name, slug=name.lower(), kind="technology", status=status)


def proposal(status="pending"):
    return TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        slug=uuid.uuid4().hex,
        action="create",
        origin="import",
        source_name="GitHub",
        proposed={},
        created_by={},
        status=status,
        reviewed_at=NOW if status != "pending" else None,
        reviewed_by={} if status != "pending" else None,
    )


def topic_job(**values):
    return TopicAnalysisJob(
        id=uuid.uuid4(),
        input_hash="a" * 64,
        input_snapshot={},
        requested_by={},
        prompt_version="v1",
        **values,
    )


def test_empty_overview_has_zero_filled_bounded_daily_series(database, admin_client, monkeypatch):
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    for days in (1, 7, 30, 90):
        response = admin_client.get(f"/v1/admin/overview?days={days}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["days"] == days
        assert len(data["activity"]) == days
        assert data["activity"][0]["date"] == (NOW.date() - timedelta(days=days - 1)).isoformat()
        assert data["activity"][-1] == {"date": "2026-09-09", "added": 0, "published": 0}
        assert all(row["added"] == row["published"] == 0 for row in data["activity"])
        assert data["analysis"] == dict(queued=0, running=0, succeeded=0, failed=0)
        assert len(data["analysis_activity"]) == days
        assert all(row["succeeded"] == row["failed"] == 0 for row in data["analysis_activity"])
        assert data["top_topics"] == []
        assert data["articles"] == data["sources_active"] == data["topics_active"] == 0


def test_daily_activity_uses_first_publication_and_utc_not_publisher_dates(database, monkeypatch):
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    with database.begin() as session:
        # First publication remains activity after unpublishing. Discovery and publisher
        # publication dates are distinct, and future data must not enter today's bucket.
        rows = []
        for discovered, published, status in [
            (START - timedelta(microseconds=1), START, "published"),
            (START, START + timedelta(hours=23, minutes=59), "unpublished"),
            (START + timedelta(days=1), None, "unpublished"),
            (NOW, NOW, "published"),
            (NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), "published"),
        ]:
            row = article(
                review_status="approved",
                publication_status=status,
                published_to_feed_at=published,
                published_at=NOW - timedelta(days=365),
            )
            row.discovered_at = discovered
            rows.append(row)
        session.add_all(rows)
    with database() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Pacific/Honolulu'"))
        data = overview.overview_metrics(session, 7)
    assert data.articles == 5 and data.articles_published == 3
    assert data.activity[0].model_dump(mode="json") == dict(date="2026-09-03", added=1, published=2)
    assert data.activity[1].added == 1
    assert data.activity[-1].added == data.activity[-1].published == 1
    assert sum(day.added for day in data.activity) == 3
    assert sum(day.published for day in data.activity) == 3
    with database() as session:
        longer = overview.overview_metrics(session, 30)
    assert longer.articles_published == data.articles_published
    assert sum(day.added for day in longer.activity) == 4


def test_inventory_and_top_topics_only_count_live_membership(database, monkeypatch):
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    with database.begin() as session:
        topics = [topic(name) for name in ("Alpha", "Beta", "Charlie", "Delta", "Echo", "Foxtrot")]
        inactive = topic("Hidden", "proposed")
        session.add_all([*topics, inactive, topic("Rejected", "rejected")])
        live = article(publication_status="published", review_status="approved")
        pending = article()
        session.add_all([live, pending])
        for i, (approval, enabled, failures) in enumerate(
            [
                ("approved", True, 0),
                ("approved", True, 2),
                ("approved", False, 8),
                ("pending", True, 2),
                ("rejected", True, 1),
            ]
        ):
            session.add(
                Source(
                    name=str(i),
                    feed_url=f"https://example.com/{i}.xml",
                    source_type="publisher",
                    approval_status=approval,
                    enabled=enabled,
                    consecutive_failures=failures,
                )
            )
        session.flush()
        for target in [*topics, inactive]:
            session.add(
                ArticleTopic(
                    article_id=live.id,
                    topic_id=target.id,
                    role="primary",
                    relevance=1,
                    evidence="Test",
                )
            )
        session.add(
            ArticleTopic(
                article_id=pending.id,
                topic_id=topics[1].id,
                role="primary",
                relevance=1,
                evidence="Test",
            )
        )
        comparison = article(publication_status="published", review_status="approved")
        session.add(comparison)
        session.flush()
        for target, role in zip(
            topics[:3], ["supporting", "comparison", "incidental"], strict=True
        ):
            session.add(
                ArticleTopic(
                    article_id=comparison.id,
                    topic_id=target.id,
                    role=role,
                    relevance=1,
                    evidence="Test",
                )
            )
    with database() as session:
        data = overview.overview_metrics(session, 7)
    assert (
        data.sources,
        data.sources_active,
        data.sources_failing,
        data.sources_pending_review,
    ) == (5, 2, 1, 1)
    assert (data.topics, data.topics_active, data.articles_pending_review) == (8, 6, 1)
    assert [(row.name, row.articles) for row in data.top_topics] == [
        ("Alpha", 2),
        ("Beta", 1),
        ("Charlie", 1),
        ("Delta", 1),
        ("Echo", 1),
    ]


def test_analysis_combines_articles_topics_relationships_and_keeps_old_queues(
    database, monkeypatch
):
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    with database.begin() as session:
        first, second = topic("First"), topic("Second")
        pending, rejected = proposal(), proposal("rejected")
        record = article()
        session.add_all([first, second, pending, rejected, record])
        session.flush()
        research = topic_job(
            topic_id=first.id, status="succeeded", outcome="enriched", finished_at=NOW
        )
        session.add_all(
            [
                research,
                topic_job(
                    proposal_id=pending.id, status="running", created_at=START - timedelta(days=40)
                ),
                topic_job(proposal_id=rejected.id, status="failed", finished_at=START),
                topic_job(
                    topic_id=second.id, status="succeeded", finished_at=START - timedelta(seconds=1)
                ),
                topic_job(
                    topic_id=first.id, status="failed", finished_at=NOW + timedelta(seconds=1)
                ),
                ArticleAnalysisJob(
                    article_id=record.id, status="queued", created_at=START - timedelta(days=40)
                ),
                ArticleAnalysisJob(article_id=record.id, status="succeeded", finished_at=NOW),
                ArticleAnalysisJob(article_id=record.id, status="failed", finished_at=NOW),
            ]
        )
        session.flush()
        for status, from_topic, to_topic in [
            ("pending", first, second),
            ("rejected", second, first),
        ]:
            session.add(
                TopicRelationProposal(
                    job_id=research.id,
                    topic_id=from_topic.id,
                    related_topic_id=to_topic.id,
                    relation="related_to",
                    explanation="Test",
                    evidence_url="https://example.com",
                    evidence_title="Evidence",
                    evidence_quote="Quote",
                    topic_snapshot={},
                    related_topic_snapshot={},
                    created_by={},
                    status=status,
                    reviewed_at=NOW if status != "pending" else None,
                    reviewed_by={} if status != "pending" else None,
                )
            )
    with database() as session:
        data = overview.overview_metrics(session, 7)
    assert data.analysis.model_dump() == dict(queued=1, running=1, succeeded=2, failed=2)
    assert data.analysis_activity[0].model_dump(mode="json") == dict(
        date="2026-09-03", succeeded=0, failed=1
    )
    assert data.analysis_activity[-1].model_dump(mode="json") == dict(
        date="2026-09-09", succeeded=2, failed=1
    )
    assert sum(day.succeeded for day in data.analysis_activity) == data.analysis.succeeded
    assert sum(day.failed for day in data.analysis_activity) == data.analysis.failed
    assert data.topic_proposals_pending == data.relationship_proposals_pending == 1
