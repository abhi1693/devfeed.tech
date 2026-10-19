"""Processing completion and publication must agree across all entry points."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks, image_tasks
from devfeed_core import analysis, editorial
from devfeed_core.article_jobs import approved_sources
from devfeed_core.article_lifecycle import (
    derive_processing_readiness,
    derive_publication_readiness,
    processing_readiness,
    reconcile_processing,
)
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.image_jobs import claim_image, request_image
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.job_retries import retry_candidate, retry_failed_job
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.services import OperationConflict
from devfeed_core.urls import fingerprint
from sqlalchemy import event, select, text
from test_automation_integration import enable, ready, seed


def facts():
    now = utcnow()
    topic = Topic(
        id=uuid.uuid4(), name="Angular", slug="angular", kind="framework", status="active"
    )
    source = Source(
        id=uuid.uuid4(), name="Publisher", source_type="publisher", approval_status="approved"
    )
    article = Article(
        id=uuid.uuid4(),
        canonical_url="https://publisher.example/article",
        title="Angular routing",
        summary="Angular routing helps developers organize navigation.",
        ai_title=None,
        ai_summary="Angular routing organizes application navigation.",
        ai_description="A guide to Angular application navigation.",
        review_status="approved",
        publication_status="unpublished",
        editorial_revision=2,
        language="en",
        content_type="tutorial",
        content_format="article",
        image_url=None,
        managed_image={},
        origins=[ArticleOrigin(source=source, source_id=source.id)],
        topic_links=[ArticleTopic(topic=topic, topic_id=topic.id, role="primary", relevance=0.9)],
    )
    taxonomy = {
        "topics": [
            {
                "id": str(topic.id),
                "name": "Angular",
                "slug": "angular",
                "kind": "framework",
                "aliases": [],
                "keywords": [],
                "description": None,
                "ai_description": None,
            }
        ],
        "tags": [],
    }
    snapshot = analysis.source_snapshot(article, None)
    candidates = analysis.analysis_candidates(taxonomy, snapshot)
    job = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="succeeded",
        outcome="applied",
        input_hash=analysis.snapshot_hash(snapshot),
        input_snapshot=snapshot,
        catalog_hash=analysis.snapshot_hash(candidates),
        catalog_snapshot=candidates,
        prompt_version=analysis.PROMPT_VERSION,
        editorial_revision=0,
        result={
            "outcome": "ready",
            "developer_relevance": "relevant",
            "page_kind": "article",
            "topics": [],
            "tags": [],
        },
        available_at=now,
        attempts=1,
    )
    article.classification_provenance = {
        "analysis_id": str(job.id),
        "input_hash": job.input_hash,
        "developer_relevance": "relevant",
        "page_kind": "article",
    }
    extraction = ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="succeeded",
        outcome="enriched",
        result={"input_url": article.canonical_url, "image_url": None},
        available_at=now,
        attempts=1,
    )
    settings = SimpleNamespace(ai_enabled=True, image_storage_enabled=False, image_public_url=None)
    return article, {
        "enrichment": extraction,
        "analysis": job,
        "taxonomy": taxonomy,
        "settings": settings,
        "now": now,
    }


def image_job(article, *, operation="store", status="running", **values):
    return ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation=operation,
        status=status,
        image_url=article.image_url if operation == "store" else None,
        storage={"input_url": article.canonical_url},
        available_at=utcnow(),
        attempts=1,
        **values,
    )


def test_ready_after_completed_image_lookup_without_an_image_and_approval_revision():
    article, values = facts()
    result = derive_publication_readiness(article, **values)
    assert result.stage == "ready" and result.blockers == []
    assert result.checks["images"].status == "unavailable"
    assert values["analysis"].editorial_revision != article.editorial_revision


@pytest.mark.parametrize("missing", ["ai_summary", "ai_description"])
def test_missing_generated_prose_blocks_even_when_source_summary_exists(missing):
    article, values = facts()
    setattr(article, missing, None)
    result = derive_publication_readiness(article, **values)
    assert result.stage == "analysis" and f"missing_{missing}" in result.blockers


def test_current_source_and_prompt_are_required_but_unrelated_editorial_revision_is_not():
    article, values = facts()
    article.summary = "Updated source evidence about Angular routing."
    result = derive_processing_readiness(article, **values)
    assert "current_analysis_required" in result.blockers
    article, values = facts()
    values["analysis"].prompt_version = "old-contract"
    assert "current_analysis_required" in derive_processing_readiness(article, **values).blockers


def test_current_insufficient_evidence_is_explicitly_blocked_not_stale():
    article, values = facts()
    values["analysis"].outcome = "insufficient_evidence"
    values["analysis"].result["outcome"] = "insufficient_evidence"
    result = derive_processing_readiness(article, **values)
    assert result.checks["analysis"].status == "unavailable"
    assert "insufficient_analysis_evidence" in result.blockers
    assert "current_analysis_required" not in result.blockers


def test_previous_extraction_does_not_complete_a_different_url():
    article, values = facts()
    values["enrichment"].result["input_url"] = "https://publisher.example/old"
    result = derive_processing_readiness(article, **values)
    assert result.stage == "enrichment" and "current_enrichment_required" in result.blockers


@pytest.mark.parametrize("operation", ["discover", "store"])
@pytest.mark.parametrize("stored", [False, True])
def test_active_image_work_blocks_even_with_a_source_url_or_previous_variants(operation, stored):
    article, values = facts()
    article.image_url = "https://publisher.example/cover.png"
    if stored:
        values["settings"].image_storage_enabled = True
        values["settings"].image_public_url = "https://images.example"
        article.managed_image = {
            "source_url": article.image_url,
            "version": "v1",
            "variants": [{"key": "cover.webp", "width": 640}],
        }
    values["storage" if operation == "store" else "discovery"] = image_job(
        article, operation=operation
    )
    result = derive_processing_readiness(article, **values)
    assert result.stage == "images" and result.state == "running"
    assert "image_processing_pending" in result.blockers


def test_failed_image_discovery_blocks_and_successful_not_found_has_explicit_fallback():
    article, values = facts()
    values["discovery"] = image_job(
        article, operation="discover", status="failed", error="unavailable"
    )
    result = derive_processing_readiness(article, **values)
    assert result.stage == "images" and "image_discovery_failed" in result.blockers
    finish_job(values["discovery"], "not_found", utcnow())
    assert derive_processing_readiness(article, **values).blockers == []


def test_storage_must_match_current_source_and_failed_current_storage_blocks():
    article, values = facts()
    article.image_url = "https://publisher.example/new.png"
    values["settings"].image_storage_enabled = True
    values["settings"].image_public_url = "https://images.example"
    article.managed_image = {
        "source_url": "https://publisher.example/old.png",
        "version": "v1",
        "variants": [{"key": "old.webp", "width": 640}],
    }
    assert "image_storage_required" in derive_processing_readiness(article, **values).blockers
    values["storage"] = image_job(article, status="failed", error="image_transform")
    assert "image_storage_failed" in derive_processing_readiness(article, **values).blockers


def test_published_and_rejected_history_needs_no_backfill_or_current_job_facts():
    article, _ = facts()
    article.publication_status = "published"
    assert derive_publication_readiness(article).model_dump() == {
        "stage": "published",
        "state": "complete",
        "blockers": [],
        "checks": {},
    }
    article.review_status = "rejected"
    assert derive_publication_readiness(article).stage == "rejected"


@pytest.mark.integration
def test_manual_admission_waits_for_images_and_stays_ready_after_approval(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        ready(session, article, topic)
        pending = ArticleImageJob(
            article_id=article.id,
            operation="discover",
            storage={"input_url": article.canonical_url},
        )
        session.add(pending)
        session.flush()
        identifier, job_id = article.id, pending.id
        editorial.decide_article(session, identifier, editorial.EditorialDecision(action="approve"))
    with (
        database.begin() as session,
        pytest.raises(OperationConflict, match="image_processing_pending"),
    ):
        editorial.decide_article(session, identifier, editorial.EditorialDecision(action="publish"))
    with database.begin() as session:
        finish_job(session.get(ArticleImageJob, job_id), "not_found", utcnow())
    with database.begin() as session:
        article = session.get(Article, identifier)
        assert processing_readiness(session, article).blockers == []
        editorial.decide_article(session, identifier, editorial.EditorialDecision(action="publish"))
        assert article.publication_status == "published"


@pytest.mark.integration
def test_automatic_policy_uses_the_same_image_gate_without_losing_applied_analysis(
    database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        pending = request_image(session, article.id)
        assert apply_publication_policy(session, article, job)["status"] == "blocked"
        assert article.publication_status == "unpublished"
        finish_job(pending, "not_found", utcnow())
        assert apply_publication_policy(session, article, job)["status"] == "published"


@pytest.mark.integration
def test_manual_classification_keeps_current_generation_without_new_ai(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        generated = (article.ai_summary, article.ai_description)
        analysis.classify_manually(
            session,
            article.id,
            analysis.ManualClassification(
                developer_relevance="relevant",
                page_kind="article",
                language="en",
                content_type="article",
                content_format="article",
                actor="operator",
                topics=[
                    {
                        "topic_id": topic.id,
                        "role": "primary",
                        "relevance": 1,
                        "evidence": topic.name,
                    }
                ],
                tags=[],
            ),
        )
        assert article.classification_provenance["origin"] == "manual"
        assert article.classification_provenance["ai_generation"]["analysis_id"] == str(job.id)
        assert reconcile_processing(session, article).stage == "review"
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1
        assert article.content_type == "article"
        assert (article.ai_summary, article.ai_description) == generated
        editorial.decide_article(session, article.id, editorial.EditorialDecision(action="approve"))
        assert processing_readiness(session, article).blockers == []
        editorial.decide_article(session, article.id, editorial.EditorialDecision(action="publish"))
        assert article.publication_status == "published"


@pytest.mark.integration
def test_stale_manual_generation_requires_operator_review_without_paid_reanalysis(
    database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        article.classification_provenance = {
            **article.classification_provenance,
            "origin": "manual",
        }
        job.prompt_version = "superseded-prompt"
        result = reconcile_processing(session, article)
        assert "manual_analysis_review_required" in result.blockers
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1


@pytest.mark.integration
def test_unchanged_insufficient_evidence_is_not_automatically_reanalyzed(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        finish_job(job, "insufficient_evidence", utcnow())
        job.result = {**job.result, "outcome": "insufficient_evidence"}
        result = reconcile_processing(session, article)
        assert "insufficient_analysis_evidence" in result.blockers
        reconcile_processing(session, article)
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1


@pytest.mark.integration
def test_reconciliation_resumes_approved_articles_without_new_ai_or_duplicate_jobs(
    database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        ready(session, article, topic)
        article.review_status = "approved"
        article.image_url = "https://publisher.example/cover.png"
        get_settings().image_storage_enabled = True
        get_settings().image_public_url = "https://images.example"
        identifier = article.id
        result = reconcile_processing(session, article)
        assert result.stage == "images"
        reconcile_processing(session, article)
        assert (
            len(
                session.scalars(
                    select(ArticleImageJob).where(ArticleImageJob.article_id == identifier)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.scalars(
                    select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
                ).all()
            )
            == 1
        )


@pytest.mark.integration
def test_old_active_analysis_cannot_hide_behind_a_newer_terminal_result(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        ready(session, article, topic)
        active = ArticleAnalysisJob(
            article_id=article.id, created_at=utcnow() - timedelta(days=1), status="running"
        )
        session.add(active)
        session.flush()
        result = processing_readiness(session, article)
        assert result.checks["analysis"].job_id == active.id
        assert "analysis_pending" in result.blockers


@pytest.mark.integration
@pytest.mark.parametrize(
    "competitor",
    ["terminal_discovery", "irrelevant_store", "active_discovery", "new_source", "published"],
)
def test_store_retry_tracks_current_asset_and_survives_other_terminal_operations(
    database, monkeypatch, competitor
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        ready(session, article, topic)
        article.image_url = "https://publisher.example/cover.png"
        get_settings().image_storage_enabled = True
        get_settings().image_public_url = "https://images.example"
        failed = ArticleImageJob(
            article_id=article.id,
            operation="store",
            status="failed",
            image_url=article.image_url,
            created_at=utcnow() - timedelta(hours=1),
        )
        session.add(failed)
        discovery = ArticleImageJob(
            article_id=article.id,
            operation="store" if competitor == "irrelevant_store" else "discover",
            status="queued" if competitor == "active_discovery" else "succeeded",
            outcome=None if competitor == "active_discovery" else "already_present",
            image_url="https://publisher.example/irrelevant.png"
            if competitor == "irrelevant_store"
            else None,
            storage={"input_url": article.canonical_url},
            created_at=utcnow() - timedelta(hours=2)
            if competitor == "active_discovery"
            else utcnow(),
        )
        session.add(discovery)
        if competitor == "new_source":
            article.image_url = "https://publisher.example/replacement.png"
        if competitor == "published":
            article.review_status, article.publication_status = "approved", "published"
        session.flush()
        eligible = session.scalar(
            select(ArticleImageJob.id).where(
                ArticleImageJob.id == failed.id, retry_candidate(ArticleImageJob)
            )
        )
        assert bool(eligible) == (competitor in {"terminal_discovery", "irrelevant_store"})
        if eligible:
            result = processing_readiness(session, article)
            assert result.checks["images"].job_id == failed.id
            successor = retry_failed_job(session, failed, "images", {"subject": "operator"})
            assert successor.operation == "store" and successor.image_url == article.image_url
            assert (
                session.scalar(
                    select(ArticleImageJob.id).where(
                        ArticleImageJob.id == failed.id, retry_candidate(ArticleImageJob)
                    )
                )
                is None
            )


@pytest.mark.integration
@pytest.mark.parametrize("source_changed", [False, True])
def test_failed_analysis_retry_carries_only_current_successful_steps(
    database, monkeypatch, source_changed
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        failed = ready(session, article, topic)
        failed.status, failed.outcome, failed.error = "failed", None, "codex_timeout"
        failed.steps = {
            "developer_relevance": {
                "status": "succeeded",
                "output": {"developer_relevance": "relevant"},
            },
            "description": {"status": "failed", "attempts": 3, "output": None},
        }
        if source_changed:
            article.summary += " Updated evidence."
        session.flush()
        successor = retry_failed_job(session, failed, "analysis", {"subject": "operator"})
        assert successor.id != failed.id
        assert set(successor.steps) == (set() if source_changed else {"developer_relevance"})
        assert failed.steps["description"]["attempts"] == 3


@pytest.mark.integration
def test_source_changed_between_specialists_stops_spending_and_queues_current_work(
    database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session)
        identifier = article.id
        analysis_id = analysis.request_analysis(session, identifier).id
    calls = []

    def complete(prompt, schema):
        calls.append(schema)
        with database.begin() as session:
            session.get(Article, identifier).summary += " Updated source evidence."
        return {
            "outcome": "ready",
            "reason": "Software engineering content.",
            "developer_relevance": "relevant",
        }

    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(analysis_id)
    with database() as session:
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        original = next(job for job in jobs if job.id == analysis_id)
        successor = next(job for job in jobs if job.id != analysis_id)
        assert len(calls) == 1
        assert original.outcome == "superseded" and successor.status == "queued"
        assert original.input_hash != successor.input_hash
        assert session.get(Article, identifier).ai_summary is None


@pytest.mark.integration
def test_stale_discovery_is_ignored_and_new_url_gets_one_successor(database, monkeypatch):
    with database.begin() as session:
        _, article, _ = seed(session)
        identifier, job_id = article.id, request_image(session, article.id).id
        original = article.canonical_url

    def fetch(url):
        with database.begin() as session:
            article = session.get(Article, identifier)
            article.canonical_url = original + "/replacement"
            article.url_hash = fingerprint(article.canonical_url)
        return FetchResult(200, b'<meta property="og:image" content="/old.png">', url)

    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(job_id))
    with database.begin() as session:
        assert session.get(Article, identifier).image_url is None
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        successor = request_image(session, identifier, automatic=True)
        assert successor.id != job_id
        assert request_image(session, identifier, automatic=True).id == successor.id


@pytest.mark.integration
def test_image_completion_and_duplicate_claim_share_article_first_locks(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        analysis_job = ready(session, article, topic)
        identifier, analysis_id = article.id, analysis_job.id
        image_id = request_image(session, identifier).id
    fetching, release, waiting = Event(), Event(), Event()
    main_thread = get_ident()

    def fetch(url):
        fetching.set()
        assert release.wait(10)
        return FetchResult(200, b'<meta property="og:image" content="/cover.png">', url)

    def capture(_connection, _cursor, statement, *_args):
        if (
            get_ident() != main_thread
            and release.is_set()
            and "FOR UPDATE" in statement
            and "articles" in statement
        ):
            waiting.set()

    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    engine = database.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(image_tasks.enrich_image, str(image_id))
            assert fetching.wait(10)
            with database.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                # Match the scheduler's analysis job -> source -> article locks.
                session.scalar(
                    select(ArticleAnalysisJob)
                    .where(ArticleAnalysisJob.id == analysis_id)
                    .with_for_update()
                )
                approved_sources(session, identifier, lock=True)
                session.scalar(select(Article).where(Article.id == identifier).with_for_update())
                release.set()
                assert waiting.wait(10)
                assert claim_image(session, image_id) is None
            worker.result(timeout=10)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", capture)
    with database() as session:
        assert session.get(Article, identifier).image_url.endswith("/cover.png")
