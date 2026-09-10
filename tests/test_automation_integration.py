import json
import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator import topic_analysis_tasks
from devfeed_core import analysis
from devfeed_core.ai_capacity import cooldown_remaining, pause_capacity
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOrigin,
    ArticlePublicationDecision,
    ArticleReview,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicReanalysis,
    utcnow,
)
from devfeed_core.publication_policy import POLICY_VERSION, apply_publication_policy
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key
from devfeed_core.topic_proposals import (
    TopicImport,
    TopicImportSubmit,
    preview_import,
    submit_import,
)
from devfeed_core.urls import fingerprint
from redis import Redis
from sqlalchemy import func, select
from test_topic_analysis import pending as pending
from test_topic_analysis import run_metadata_research
from test_topic_relationships import catalog as catalog
from test_topic_relationships import execute, queue, suggestion

pytestmark = pytest.mark.integration


def enable(monkeypatch, **flags):
    for key, value in {"AI_ENABLED": True, **flags}.items():
        monkeypatch.setenv("DEVFEED_" + key, str(value).lower())
    monkeypatch.setenv("DEVFEED_CODEX_APP_SERVER_URL", "ws://127.0.0.1:4555")
    monkeypatch.setenv("DEVFEED_CODEX_MODEL", "test-model")
    get_settings.cache_clear()


def seed(session, *, mode="manual", name="Angular", topic=True):
    identifier = uuid.uuid4()
    url = f"https://example.com/{identifier}"
    source = Source(
        name="Publisher",
        feed_url=url + "/rss",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        publication_policy=mode,
    )
    article = Article(
        canonical_url=url,
        url_hash=fingerprint(url),
        title=f"{name} routing",
        summary=f"{name} routing helps developers build navigation and organize applications.",
        discovered_at=utcnow() - timedelta(minutes=10),
    )
    target = (
        Topic(name=name, slug=name.lower(), kind="technology", status="active") if topic else None
    )
    session.add_all([source, article, *([target] if target else [])])
    session.flush()
    session.add(
        ArticleOrigin(
            article_id=article.id, source_id=source.id, entry_key=str(identifier), original_url=url
        )
    )
    session.flush()
    return source, article, target


def ready(session, article, topic):
    job = analysis.request_analysis(session, article.id)
    result = analysis.AnalysisResult(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="tutorial",
        content_format="article",
        ai_summary="A guide to application routing.",
        ai_description=None,
        topics=[
            {"topic_id": topic.id, "role": "primary", "relevance": 0.9, "evidence": topic.name}
        ],
        tags=[],
        reasons=[],
    )
    job.result = result.model_dump(mode="json")
    analysis.apply_analysis(session, article, job, result)
    return job


def test_import_research_is_opt_in_bounded_and_durable(database, monkeypatch):
    enable(monkeypatch, AUTO_RESEARCH_IMPORTS=True, AUTOMATION_BATCH_SIZE=1)
    with database.begin() as session:
        body = TopicImport(
            format="json",
            source_name="Trusted operator",
            content=json.dumps(
                [
                    {"name": name, "slug": name.lower(), "kind": "technology"}
                    for name in ["First", "Second"]
                ]
            ),
        )
        preview = preview_import(session, body)
        proposals = submit_import(
            session,
            TopicImportSubmit(**body.model_dump(), preview_token=preview.preview_token),
            {"subject": "operator"},
        )
        assert all(proposal.research_requested for proposal in proposals)
    assert schedule_automation(database)["topic_research_scheduled"] == 1
    assert schedule_automation(database)["topic_research_scheduled"] == 1
    assert schedule_automation(database)["topic_research_scheduled"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicAnalysisJob)) == 2


def test_topic_changes_scan_in_batches_and_skip_unrelated_and_reviewed_articles(
    database, monkeypatch
):
    enable(monkeypatch, AUTO_REANALYZE_TOPICS=True, AUTOMATION_BATCH_SIZE=1)
    with database.begin() as session:
        _, first, _ = seed(session, topic=False)
        _, unrelated, _ = seed(session, topic=False, name="Databases")
        _, rejected, _ = seed(session, topic=False)
        rejected.review_status = "rejected"
        session.add(Topic(name="Angular", slug="angular", kind="framework", status="active"))
        ids = first.id, unrelated.id, rejected.id
    counts = [schedule_automation(database) for _ in range(4)]
    assert sum(item["articles_reanalyzed"] for item in counts) == 1
    assert all(item["reanalysis_scanned"] <= 1 for item in counts)
    with database.begin() as session:
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        assert [job.article_id for job in jobs] == [ids[0]]
        assert session.scalar(select(TopicReanalysis)).finished_at
        topic = session.scalar(select(Topic))
        topic.description = "Metadata-only edits do not change candidate selection."
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicReanalysis)) == 1


def test_catalog_change_can_reanalyze_identical_article_content(database, monkeypatch):
    enable(monkeypatch, AUTO_REANALYZE_TOPICS=True)
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        first = analysis.request_analysis(session, article.id, automatic=True)
        finish_job(first, "insufficient_evidence", utcnow())
        original_hash = first.input_hash
        assert analysis.request_analysis(session, article.id, automatic=True) is None
        article_id = article.id
    with database.begin() as session:
        session.add(Topic(name="Angular", slug="angular", kind="framework", status="active"))
    assert schedule_automation(database)["articles_reanalyzed"] == 1
    with database() as session:
        jobs = session.scalars(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article_id)
        ).all()
        assert len(jobs) == 2 and {job.input_hash for job in jobs} == {original_hash}
        assert len({job.catalog_hash for job in jobs}) == 2


def test_removed_topic_alias_still_triggers_reanalysis(database, monkeypatch):
    enable(monkeypatch, AUTO_REANALYZE_TOPICS=False)
    with database.begin() as session:
        _, article, topic = seed(session, name="Routing")
        topic.aliases = ["Angular"]
        article.title = "Angular"
        article.summary = "Angular helps developers build and organize their applications."
        job = analysis.request_analysis(session, article.id, automatic=True)
        finish_job(job, "insufficient_evidence", utcnow())
        topic_id = topic.id
    enable(monkeypatch, AUTO_REANALYZE_TOPICS=True)
    with database.begin() as session:
        topic = session.get(Topic, topic_id)
        topic.aliases = []
    assert schedule_automation(database)["articles_reanalyzed"] == 1


def test_topic_removed_during_inference_supersedes_and_requeues(database, monkeypatch):
    from types import SimpleNamespace

    from devfeed_aggregator import analysis_tasks

    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        topic_id = topic.id
        job_id = analysis.request_analysis(session, article.id).id

    def complete(*args):
        with database.begin() as session:
            session.get(Topic, topic_id).status = "proposed"
        return dict(
            outcome="ready",
            developer_relevance="relevant",
            language="en",
            content_type="tutorial",
            content_format="article",
            ai_summary=None,
            ai_description=None,
            tags=[],
            reasons=[],
            topics=[
                dict(topic_id=str(topic_id), role="primary", relevance=0.9, evidence="Angular")
            ],
        )

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job_id)
    with database() as session:
        original = session.get(ArticleAnalysisJob, job_id)
        assert original.outcome == "superseded" and original.status == "succeeded"
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        assert len(jobs) == 2
        assert next(job for job in jobs if job.id != job_id).status == "queued"


@pytest.mark.parametrize(
    "mode,status,publication",
    [
        ("manual", "blocked", "unpublished"),
        ("preview", "would_publish", "unpublished"),
        ("auto", "published", "published"),
    ],
)
def test_source_policy_preview_and_publication_share_checks_and_keep_audit(
    database, mode, status, publication
):
    with database.begin() as session:
        _, article, topic = seed(session, mode=mode)
        job = ready(session, article, topic)
        decision = apply_publication_policy(session, article, job)
        assert decision["status"] == status
        assert article.publication_status == publication
        assert decision["policy_version"] == POLICY_VERSION
        article_id = article.id
    with database() as session:
        reviews = session.scalars(
            select(ArticleReview).where(ArticleReview.article_id == article_id)
        ).all()
        assert len(reviews) == (2 if mode == "auto" else 0)
        assert all(row.automation["policy_version"] == POLICY_VERSION for row in reviews)
        assert session.scalar(select(func.count()).select_from(ArticlePublicationDecision)) == 1


def test_human_edit_blocks_automatic_publication(database):
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        session.add(
            ArticleReview(article_id=article.id, action="edit", actor="operator", revision=0)
        )
        job = ready(session, article, topic)
        decision = apply_publication_policy(session, article, job)
        assert "human_review_required" in decision["reasons"]
        assert article.publication_status == "unpublished"


def test_preview_is_idempotent_and_preserved_after_policy_change(database):
    with database.begin() as session:
        source, article, topic = seed(session, mode="preview")
        job = ready(session, article, topic)
        apply_publication_policy(session, article, job)
        session.flush()
        apply_publication_policy(session, article, job)
        source.publication_policy = "auto"
        source.publication_policy_revision += 1
        apply_publication_policy(session, article, job)
    with database() as session:
        decisions = session.scalars(
            select(ArticlePublicationDecision).order_by(ArticlePublicationDecision.created_at)
        ).all()
        assert [row.decision["status"] for row in decisions] == ["would_publish", "published"]


def test_policy_api_requires_preview_and_checks_revision(admin_client, database):
    with database.begin() as session:
        source, _, _ = seed(session)
        identifier = str(source.id)
    path = f"/v1/admin/sources/{identifier}/publication-policy"
    assert admin_client.put(path, json={"mode": "auto", "expected_revision": 0}).status_code == 409
    response = admin_client.put(path, json={"mode": "preview", "expected_revision": 0})
    assert response.status_code == 200, response.text
    assert response.json()["publication_policy_revision"] == 1
    assert admin_client.put(path, json={"mode": "auto", "expected_revision": 0}).status_code == 409
    assert admin_client.put(path, json={"mode": "auto", "expected_revision": 1}).status_code == 200
    history = admin_client.get(f"/v1/admin/automation/sources/{identifier}/policies").json()
    assert history["total"] == 2 and history["items"][0]["actor"] == "integration-admin"


def test_unverified_topic_remains_pending_and_visible_on_dashboard(
    admin_client, database, pending, monkeypatch
):
    enable(monkeypatch, AUTO_APPROVE_TOPICS=True)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda citations: {
            "version": VERIFICATION_VERSION,
            "checks": {
                citation_key(url, quote): {"status": "unverified", "reason": "quote_not_found"}
                for url, quote in citations
            },
        },
    )
    job_id = run_metadata_research(admin_client, pending, monkeypatch)
    topic_analysis_tasks._analyze(job_id)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        job = session.get(TopicAnalysisJob, job_id)
        assert proposal.status == "pending" and job.status == "succeeded"
        assert "auto_approval" not in job.result  # Full identity review has not run yet.
    response = admin_client.get("/v1/admin/overview")
    assert response.status_code == 200, response.text
    blockers = {group["code"]: group for group in response.json()["automation"]["blockers"]}
    assert blockers["evidence_unverified"]["count"] == 1


def test_capacity_pause_is_shared_and_cannot_be_shortened(database):
    with Redis.from_url(get_settings().redis_url) as connection:
        assert cooldown_remaining(connection) == 0
        pause_capacity(600)
        pause_capacity(30)
        assert 590 <= cooldown_remaining(connection) <= 600


def test_relationship_blockers_match_each_proposals_own_citation(
    admin_client, catalog, monkeypatch
):
    job = queue(admin_client, catalog[0])
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda citations: {
            "version": VERIFICATION_VERSION,
            "checks": {
                citation_key(url, quote): {
                    "url": url,
                    "quote": quote,
                    "status": "verified" if index == 0 else "unverified",
                }
                for index, (url, quote) in enumerate(citations)
            },
        },
    )
    execute(
        monkeypatch,
        job,
        [
            suggestion(catalog),
            suggestion(catalog, related_topic_id=str(catalog[2]), evidence_quote="Another quote"),
        ],
    )
    response = admin_client.get("/v1/admin/overview")
    assert response.status_code == 200, response.text
    blockers = {item["code"]: item for item in response.json()["automation"]["blockers"]}
    assert blockers["relationship_evidence_unverified"]["count"] == 1
    assert blockers["relationship_evidence_unverified"]["targets"][0]["title"] == "React → Vue"


def test_overview_counts_actual_autonomous_publications(admin_client, database):
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        apply_publication_policy(session, article, job)
    response = admin_client.get("/v1/admin/overview")
    assert response.status_code == 200, response.text
    metrics = response.json()["automation"]
    assert metrics["published_without_intervention"] == metrics["published_in_window"] == 1
    assert metrics["automatic_publication_percent"] == 100
    assert 590 <= metrics["median_ingestion_to_publication_seconds"] <= 620


def test_recovery_rejects_stale_revision_and_coalesces_active_work(
    admin_client, database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session)
        identifier = article.id
    path = f"/v1/admin/automation/articles/{identifier}/analyze"
    assert admin_client.post(path, json={"expected_revision": 1}).status_code == 409
    first = admin_client.post(path, json={"expected_revision": 0})
    second = admin_client.post(path, json={"expected_revision": 0})
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_recovery_cannot_publish_with_a_stale_catalog(admin_client, database):
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        ready(session, article, topic)
        topic.aliases = ["New alias"]
        identifier = article.id
    response = admin_client.post(
        f"/v1/admin/automation/articles/{identifier}/evaluate", json={"expected_revision": 0}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert "current_catalog_required" in response.json()["decision"]["reasons"]
    with database() as session:
        assert session.get(Article, identifier).publication_status == "unpublished"


def test_attempt_telemetry_is_idempotent_and_available_in_admin_history(admin_client, database):
    import time
    from types import SimpleNamespace

    from devfeed_aggregator.analysis_telemetry import record_attempt

    with database.begin() as session:
        _, article, _ = seed(session)
        job = analysis.request_analysis(session, article.id)
        job.usage = {"capacity_deferrals": 2}
        identifier = job.id
    client = SimpleNamespace(usage={"totalTokens": 120, "inputTokens": 100, "outputTokens": 20})
    started = time.perf_counter() - 1
    for attempt in (1, 1, 2):
        record_attempt(database, ArticleAnalysisJob, identifier, client, started, attempt=attempt)
    response = admin_client.get(f"/v1/admin/jobs/analysis/{identifier}")
    assert response.status_code == 200, response.text
    details = response.json()["details"]
    assert details["usage"]["totalTokens"] == 240
    assert details["usage"]["capacity_deferrals"] == 2
    assert len(details["usage"]["attempts"]) == 2
    assert details["duration_ms"] >= 2000
