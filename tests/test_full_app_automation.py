"""Full-mode lifecycle against disposable PostgreSQL and Redis, with bounded external fakes."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks, article_tasks, tasks
from devfeed_core import analysis, services
from devfeed_core.article_automation import schedule_article_automation, schedule_source_admission
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticlePublicationDecision,
    ArticleReview,
    ArticleTag,
    IngestionJob,
    Source,
    SourceReview,
    Tag,
    Topic,
    TopicProposal,
    utcnow,
)
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.schemas import SourceCreate
from devfeed_core.tag_topic_discovery import schedule_tag_topic_discovery
from sqlalchemy import func, select
from test_article_enrichment_integration import PAGE
from test_automation_integration import enable, ready, seed

pytestmark = pytest.mark.integration


def full(monkeypatch, **flags):
    enable(monkeypatch, FULL_AUTOMATION=True, **flags)


def due(database, identifier):
    with database.begin() as session:
        session.get(Article, identifier).automation_next_check_at = utcnow() - timedelta(seconds=1)


def test_validated_source_to_public_article_and_exact_tag_association(
    database, monkeypatch, client
):
    full(monkeypatch, AUTO_LINK_TAGS=False)
    assert get_settings().auto_link_tags
    with database.begin() as session:
        topic = Topic(name="Angular", slug="angular", kind="technology", status="active")
        session.add(topic)
        source = services.create_source(
            session,
            services.validate_source(
                SourceCreate(
                    name="Publisher",
                    feed_url="https://example.com/rss",
                    source_type="publisher",
                )
            ),
        )
        topic_id, source_id = topic.id, source.id
        ingestion_id = session.scalar(select(IngestionJob.id))
        assert source.approval_status == "approved"
        assert source.publication_policy == "manual"
    feed = b"""<rss version="2.0"><channel><title>Publisher</title><link>https://example.com</link>
    <description>Developer articles</description><item><guid>angular-guide</guid>
    <link>https://example.com/angular</link><title>Angular routing</title>
    <category>Angular</category>
    <description>Angular routing helps developers build navigation and organize applications.
    </description>
    </item></channel></rss>"""
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, feed, a[0]))
    tasks.ingest(str(ingestion_id))
    with database() as session:
        identifier = session.scalar(select(Article.id))
        enrichment_id = session.scalar(select(ArticleEnrichmentJob.id))
    monkeypatch.setattr(
        article_tasks,
        "fetch_article_page",
        lambda url: FetchResult(
            200,
            PAGE.replace(b"Python", b"Angular"),
            url,
        ),
    )
    article_tasks.enrich_article(str(enrichment_id))
    with database() as session:
        analysis_id = session.scalar(select(ArticleAnalysisJob.id))
    result = {
        "outcome": "ready",
        "developer_relevance": "relevant",
        "language": "en",
        "content_type": "tutorial",
        "content_format": "article",
        "ai_summary": "Angular routing helps developers organize and navigate their applications.",
        "ai_description": None,
        "topics": [
            {"topic_id": str(topic_id), "role": "primary", "relevance": 0.9, "evidence": "Angular"}
        ],
        "tags": [],
        "reasons": [],
    }
    monkeypatch.setattr(
        analysis_tasks,
        "CodexClient",
        lambda settings: SimpleNamespace(
            complete=lambda *args: result,
        ),
    )
    analysis_tasks.analyze_article(str(analysis_id))
    schedule_tag_topic_discovery(database)
    # Duplicate deliveries and scheduler passes cannot duplicate decisions or revive completed work.
    analysis_tasks.analyze_article(str(analysis_id))
    assert schedule_article_automation(database)["articles_checked"] == 0
    with database() as session:
        article = session.get(Article, identifier)
        assert (article.review_status, article.publication_status) == ("approved", "published")
        assert article.topic_links[0].topic_id == topic_id
        assert session.scalar(select(Tag).where(Tag.slug == "angular")).topic_id == topic_id
        assert [tag.slug for tag in article.tags] == ["angular"]
        assert session.scalar(select(func.count()).select_from(ArticleReview)) == 2
        assert session.scalar(select(func.count()).select_from(ArticlePublicationDecision)) == 1
        assert session.get(Source, source_id).publication_policy == "manual"
        assert session.scalar(select(SourceReview)).actor == "devfeed:source-automation"
    response = client.get(f"/v1/articles/{identifier}")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("mode", ["manual", "preview", "auto"])
def test_full_mode_publishes_existing_valid_analysis_without_changing_source_policy(
    database, monkeypatch, mode
):
    full(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session, mode=mode)
        ready(session, article, topic)
        identifier, source_id = article.id, source.id
    assert schedule_article_automation(database)["articles_published"] == 1
    assert schedule_article_automation(database)["articles_checked"] == 0
    with database() as session:
        assert session.get(Article, identifier).publication_status == "published"
        assert session.get(Source, source_id).publication_policy == mode


@pytest.mark.parametrize("failure", ["unrelated", "uncertain", "insufficient", "failed"])
def test_terminal_analysis_becomes_attributed_rejection(database, monkeypatch, failure):
    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        if failure == "failed":
            job.status, job.error, job.attempts = "failed", "invalid_analysis_result", 3
        elif failure == "insufficient":
            job.outcome = "insufficient_evidence"
        elif failure == "unrelated":
            article.classification_provenance = {
                **article.classification_provenance,
                "developer_relevance": "unrelated",
            }
            job.result = {**job.result, "developer_relevance": "unrelated"}
        else:
            job.result = {**job.result, "reasons": ["Evidence remains uncertain"]}
        identifier = article.id
    assert schedule_article_automation(database)["articles_rejected"] == 1
    with database() as session:
        assert session.get(Article, identifier).review_status == "rejected"
        review = session.scalar(select(ArticleReview))
        assert review.action == "reject" and review.automation["reasons"]
        assert review.actor == "devfeed:automatic-publication"
    assert schedule_article_automation(database)["articles_checked"] == 0


def test_empty_page_fallback_is_rejected_without_an_ai_loop(database, monkeypatch):
    full(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session)
        article.summary = "Too short"
        identifier = article.id
    assert schedule_article_automation(database)["articles_checked"] == 1
    with database.begin() as session:
        job = session.scalar(select(ArticleEnrichmentJob))
        finish_job(job, "not_found", utcnow())
    due(database, identifier)
    assert schedule_article_automation(database)["articles_rejected"] == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 0
        assert session.scalar(select(ArticlePublicationDecision)).decision["reasons"] == [
            "insufficient_source_text"
        ]


@pytest.mark.parametrize("state", ["queued", "running"])
def test_active_jobs_and_capacity_deferrals_are_not_rejected(database, monkeypatch, state):
    full(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session)
        job = analysis.request_analysis(session, article.id)
        job.status, job.error = state, "codex_usage_limit"
        article.automation_started_at = utcnow() - timedelta(days=3)
    assert schedule_article_automation(database)["articles_rejected"] == 0
    with database() as session:
        assert session.scalar(select(Article)).review_status == "pending"
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 1


def test_pending_topic_waits_then_new_catalog_automatically_reanalyzes(database, monkeypatch):
    full(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        job = analysis.request_analysis(session, article.id)
        job.result = {"developer_relevance": "relevant"}
        finish_job(job, "insufficient_evidence", utcnow())
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="angular",
                action="create",
                origin="import",
                source_name="Test",
                created_by={},
                proposed={"name": "Angular", "slug": "angular"},
            )
        )
        identifier = article.id
    assert schedule_article_automation(database)["articles_rejected"] == 0
    due(database, identifier)
    with database.begin() as session:
        session.add(Topic(name="Angular", slug="angular", kind="technology", status="active"))
    assert schedule_article_automation(database)["articles_rejected"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 2
        assert session.scalar(select(Article)).review_status == "pending"


def test_topic_wait_is_bounded(database, monkeypatch):
    full(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        article.automation_started_at = utcnow() - timedelta(hours=25)
        job = analysis.request_analysis(session, article.id)
        job.result = {"developer_relevance": "relevant"}
        finish_job(job, "insufficient_evidence", utcnow())
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="angular",
                action="create",
                origin="import",
                source_name="Test",
                created_by={},
                proposed={"name": "Angular", "slug": "angular"},
            )
        )
    assert schedule_article_automation(database)["articles_rejected"] == 1


@pytest.mark.parametrize("change", ["summary", "revision", "catalog"])
def test_stale_analysis_is_replaced_before_any_decision(database, monkeypatch, change):
    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        if change == "summary":
            article.summary += " Updated source evidence for developers."
        elif change == "revision":
            article.editorial_revision += 1
        else:
            topic.aliases = ["Angular framework"]
        assert apply_publication_policy(session, article, job)["status"] == "blocked"
    assert schedule_article_automation(database)["articles_published"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 2
        assert session.scalar(select(Article)).review_status == "pending"


def test_full_mode_off_preserves_manual_behavior(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session)
        source.approval_status = "pending"
        identifier = article.id
    assert schedule_source_admission(database) == 0
    assert schedule_article_automation(database)["articles_checked"] == 0
    with database() as session:
        assert session.get(Article, identifier).review_status == "pending"


@pytest.mark.parametrize(
    "protection", ["disabled", "rejected_source", "rejected_article", "approved_article"]
)
def test_full_mode_respects_disabled_sources_and_completed_decisions(
    database, monkeypatch, protection
):
    full(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session)
        ready(session, article, topic)
        if protection == "disabled":
            source.enabled = False
        elif protection == "rejected_source":
            source.approval_status = "rejected"
        else:
            article.review_status = protection.split("_")[0]
    assert schedule_article_automation(database)["articles_checked"] == 0


def test_backlog_admission_and_article_scans_are_bounded(database, monkeypatch):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=1)
    with database.begin() as session:
        for name in ("Angular", "Python"):
            source, article, _ = seed(session, name=name)
            source.approval_status = "pending"
    assert schedule_source_admission(database) == 1
    assert schedule_source_admission(database) == 1
    assert schedule_source_admission(database) == 0
    assert schedule_article_automation(database)["articles_checked"] == 1
    assert schedule_article_automation(database)["articles_checked"] == 1
    assert schedule_article_automation(database)["articles_checked"] == 0


def test_source_labels_propose_once_without_creating_topics_or_guessing_associations(
    database, monkeypatch
):
    full(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        tag = Tag(name="Angular", slug="angular")
        session.add(tag)
        session.flush()
        session.add(ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"))
        job = analysis.request_analysis(session, article.id)
        job.result = {"developer_relevance": "relevant"}
        finish_job(job, "insufficient_evidence", utcnow())
        identifier = article.id
    assert schedule_article_automation(database)["source_topics_proposed"] == 1
    due(database, identifier)
    assert schedule_article_automation(database)["source_topics_proposed"] == 0
    with database.begin() as session:
        proposal = session.scalar(select(TopicProposal))
        assert proposal.research_requested and proposal.status == "pending"
        assert proposal.origin == "article_enrichment"
        assert proposal.evidence[0]["article_id"] == str(identifier)
        assert session.scalar(select(func.count()).select_from(Topic)) == 0
        assert session.scalar(select(Tag)).topic_id is None
        proposal.status, proposal.reviewed_at, proposal.reviewed_by = "rejected", utcnow(), {}
        assert session.scalar(select(Article)).review_status == "pending"
    due(database, identifier)
    result = schedule_article_automation(database)
    assert result["source_topics_proposed"] == 0 and result["articles_rejected"] == 1


def test_full_mode_can_finish_freshly_analyzed_human_draft(database, monkeypatch):
    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        session.add(
            ArticleReview(article_id=article.id, action="edit", actor="operator", revision=0)
        )
        ready(session, article, topic)
    assert schedule_article_automation(database)["articles_published"] == 1


def test_source_api_reports_effective_full_mode_and_admits_submission(
    database, monkeypatch, client, admin_client
):
    full(monkeypatch)
    response = admin_client.post(
        "/v1/admin/sources",
        json={"feed_url": "https://example.com/rss", "source_type": "publisher"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["approval_status"] == "approved"
    detail = admin_client.get(f"/v1/admin/sources/{response.json()['id']}")
    assert detail.json()["full_automation"] is True
