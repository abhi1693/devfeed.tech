"""Processing moves queue guarded task retries rather than assigning a stage."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from devfeed_aggregator import article_tasks, image_storage_tasks, image_tasks
from devfeed_aggregator.article_pages import PageArticle
from devfeed_core import editorial
from devfeed_core.analysis import apply_analysis
from devfeed_core.article_automation import schedule_article_automation
from devfeed_core.article_lifecycle import processing_readiness
from devfeed_core.article_processing_retry import request_processing_retry
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.job_retries import retry_candidate
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleReview,
    FeedNotificationEvent,
    utcnow,
)
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.services import OperationConflict
from sqlalchemy import func, select
from test_admin_auth import complete
from test_admin_auth import oidc_app as oidc_app
from test_analysis import result as analysis_result
from test_article_pipeline import make_article


def retry_body(client, identifier, target):
    detail = client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    job = detail["jobs"][target]
    return {
        "expected_revision": detail["editorial_revision"],
        "target": target,
        "expected_job_id": job["id"] if job else None,
    }


def test_retry_route_requires_admin_and_csrf_before_database_access(oidc_app):
    path = f"/v1/admin/articles/{uuid.uuid4()}/pipeline/retry"
    body = {"expected_revision": 0, "target": "enrichment", "expected_job_id": None}
    assert oidc_app.client.post(path, json=body).status_code == 401
    assert complete(oidc_app).status_code == 302
    assert oidc_app.client.post(path, json=body).status_code == 403


@pytest.mark.integration
def test_retry_queues_selected_task_and_rejects_stale_replays_and_publication(
    admin_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        identifier = make_article(session).id
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert detail["retry_targets"] == ["enrichment", "analysis", "images"]
    body = retry_body(admin_client, identifier, "images")
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["stage"] == "images" and current["state"] == "waiting"
    assert current["editorial_revision"] == body["expected_revision"]
    assert current["retry_targets"] == ["enrichment", "analysis"]
    with database.begin() as session:
        job = session.get(ArticleImageJob, uuid.UUID(current["jobs"]["images"]["id"]))
        assert job.storage["article_refresh"] is True
        assert session.get(Article, identifier).review_status == "approved"
        audit = session.scalar(select(ArticleReview).where(ArticleReview.action == "retry"))
        assert audit.actor == "integration-admin" and audit.automation["job_id"] == str(job.id)
        with pytest.raises(OperationConflict, match="image_processing_pending"):
            editorial.decide_article(
                session, identifier, editorial.EditorialDecision(action="publish")
            )
        finish_job(job, "not_found", utcnow())
    # Even a fast completion cannot make the original card's POST valid again.
    assert (
        admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body).status_code
        == 409
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 1


@pytest.mark.integration
def test_failed_analysis_retry_preserves_verified_steps_without_changing_revision(
    admin_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        article = make_article(session, state="analysis")
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article.id)
        )
        job.status = "failed"
        job.steps = {
            "developer_relevance": {
                "status": "succeeded",
                "input_hash": "proof",
                "output": {
                    "outcome": "ready",
                    "developer_relevance": "relevant",
                    "reason": "Source supported",
                },
            },
            "overview": {"status": "failed", "attempts": 3},
        }
        identifier, original_id = article.id, job.id
    body = retry_body(admin_client, identifier, "analysis")
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["editorial_revision"] == body["expected_revision"]
    with database() as session:
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        original = next(job for job in jobs if job.id == original_id)
        successor = next(job for job in jobs if job.id != original_id)
        assert original.status == "failed" and successor.status == "queued"
        assert set(successor.steps) == {"developer_relevance"}


@pytest.mark.integration
@pytest.mark.parametrize("stage,target", [("analysis", "enrichment"), ("images", "analysis")])
def test_earlier_retry_preserves_independent_active_lease(
    admin_client, database, monkeypatch, stage, target
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    model = ArticleAnalysisJob if stage == "analysis" else ArticleImageJob
    with database.begin() as session:
        article = make_article(session, state=stage)
        active = session.scalar(select(model).where(model.article_id == article.id))
        active.lease_token = token = uuid.uuid4()
        active.lease_until = utcnow() + timedelta(minutes=3)
        identifier, active_id = article.id, active.id
    body = retry_body(admin_client, identifier, target)
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == target
    with database() as session:
        active = session.get(model, active_id)
        assert active.status == "running" and active.lease_token == token
        assert active.lease_until > utcnow()


@pytest.mark.integration
def test_inline_refetch_source_change_fences_the_independent_older_analysis(
    admin_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        article = make_article(session, state="analysis")
        identifier = article.id
        analysis_id = session.scalar(
            select(ArticleAnalysisJob.id).where(ArticleAnalysisJob.article_id == identifier)
        )
    body = retry_body(admin_client, identifier, "enrichment")
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    extraction_id = response.json()["jobs"]["enrichment"]["id"]
    page = PageArticle(
        title="Python task tracing with new runtime evidence",
        summary="Updated source explains Python task snapshots and stack inspection.",
        author=None,
        published_at=None,
        image_url=None,
        language="en",
        text_source="body",
        evidence={"image_url": None},
        text="Python task snapshots expose updated stack inspection evidence.",
    )
    monkeypatch.setattr(article_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(article_tasks, "fetch_article_page", lambda url: FetchResult(200, b"", url))
    monkeypatch.setattr(article_tasks, "extract_article", lambda *_: page)
    article_tasks.enrich_article(extraction_id)
    with database.begin() as session:
        article, previous = (
            session.get(Article, identifier),
            session.get(ArticleAnalysisJob, analysis_id),
        )
        assert article.editorial_revision == body["expected_revision"] + 1
        assert previous.status == "running"
        apply_analysis(session, article, previous, analysis_result())
        assert previous.outcome == "superseded"
        assert article.ai_summary is None and article.review_status == "pending"
        assert article.title == page.title


@pytest.mark.integration
def test_current_analysis_cannot_reject_while_source_refetch_is_pending(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    with database.begin() as session:
        article = make_article(session, state="review")
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article.id)
        )
        job.result = {**job.result, "developer_relevance": "unrelated"}
        extraction = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == article.id)
        )
        request_processing_retry(
            session,
            article.id,
            target="enrichment",
            expected_revision=article.editorial_revision,
            expected_job_id=extraction.id,
            actor={"subject": "operator"},
        )
        assert "enrichment_pending" in processing_readiness(session, article).blockers
        assert apply_publication_policy(session, article, job)["status"] == "blocked"
        assert article.review_status == "pending"


@pytest.mark.integration
def test_concurrent_requests_with_one_baseline_queue_one_task(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        article = make_article(session)
        identifier, revision = article.id, article.editorial_revision
    barrier = Barrier(2)

    def request():
        barrier.wait(timeout=10)
        try:
            with database.begin() as session:
                request_processing_retry(
                    session,
                    identifier,
                    target="images",
                    expected_revision=revision,
                    expected_job_id=None,
                    actor={"subject": "operator"},
                )
            return "queued"
        except OperationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: request(), range(2))) == ["conflict", "queued"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 1
        assert session.scalar(select(func.count()).select_from(ArticleReview)) == 1


@pytest.mark.integration
def test_published_image_retry_unpublishes_atomically_preserves_ai_and_feed_history(
    admin_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    with database.begin() as session:
        article = make_article(session, state="published")
        identifier, published = article.id, article.published_to_feed_at
        original_ai = (
            article.ai_summary,
            article.ai_description,
            dict(article.classification_provenance),
        )
    body = retry_body(admin_client, identifier, "images")
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "images"
    assert response.json()["editorial_revision"] == body["expected_revision"] + 1
    schedule_article_automation(database)
    with database.begin() as session:
        article = session.get(Article, identifier)
        assert article.publication_status == "unpublished" and article.review_status == "approved"
        assert (
            article.ai_summary,
            article.ai_description,
            article.classification_provenance,
        ) == original_ai
        assert article.published_to_feed_at == published
        image = session.scalar(
            select(ArticleImageJob).where(ArticleImageJob.article_id == identifier)
        )
        finish_job(image, "not_found", utcnow())
        assert processing_readiness(session, article).stage == "ready"
    schedule_article_automation(database)
    with database.begin() as session:
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 1
        editorial.decide_article(session, identifier, editorial.EditorialDecision(action="publish"))
        assert session.get(Article, identifier).published_to_feed_at == published
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 0
        assert all(
            review.automation
            for review in session.scalars(
                select(ArticleReview).where(ArticleReview.action != "publish")
            )
        )


@pytest.mark.integration
@pytest.mark.parametrize("unsafe", ["cutoff", "source", "stale_ai"])
def test_published_unsafe_image_retry_conflicts_before_unpublishing(
    admin_client, database, monkeypatch, unsafe
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "ai_content_not_before", datetime(2026, 10, 1, tzinfo=UTC))
    with database.begin() as session:
        article = make_article(session, state="published")
        identifier = article.id
        if unsafe == "cutoff":
            article.published_at = datetime(2020, 1, 1, tzinfo=UTC)
        elif unsafe == "source":
            article.origins[0].source.approval_status = "rejected"
        else:
            session.scalar(
                select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
            ).prompt_version = "old-prompt"
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert "images" not in detail["retry_targets"]
    body = retry_body(admin_client, identifier, "images")
    assert (
        admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body).status_code
        == 409
    )
    with database() as session:
        assert session.get(Article, identifier).publication_status == "published"
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 0


@pytest.mark.integration
def test_retry_body_revision_and_terminal_rejection_guards(admin_client, database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        identifier = make_article(session).id
        rejected = make_article(session, state="rejected").id
    body = retry_body(admin_client, identifier, "images")
    path = f"/v1/admin/articles/{identifier}/pipeline/retry"
    assert admin_client.post(path, json={**body, "expected_revision": 999}).status_code == 409
    assert (
        admin_client.post(path, json={"expected_revision": 0, "target": "images"}).status_code
        == 422
    )
    for target in ("ingested", "review", "ready", "published", "rejected"):
        assert admin_client.post(path, json={**body, "target": target}).status_code == 422
    rejected_body = retry_body(admin_client, rejected, "enrichment")
    assert (
        admin_client.post(
            f"/v1/admin/articles/{rejected}/pipeline/retry", json=rejected_body
        ).status_code
        == 409
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 0


@pytest.mark.integration
def test_refresh_discovery_preserves_old_image_and_rejects_stale_result(database, monkeypatch):
    from devfeed_core.image_jobs import request_image

    with database.begin() as session:
        article = make_article(session)
        article.image_url = "https://publisher.example/old.png"
        identifier = article.id
        job = request_image(session, identifier, refresh=True)
        job_id = job.id
        assert article.image_url.endswith("old.png")

    def fetch(url):
        with database.begin() as session:
            session.get(Article, identifier).image_url = "https://publisher.example/newer.png"
        return FetchResult(200, b'<meta property="og:image" content="/stale.png">', url)

    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(job_id))
    with database() as session:
        assert session.get(Article, identifier).image_url.endswith("newer.png")
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"


@pytest.mark.integration
@pytest.mark.parametrize("storage_enabled_after_failure", [False, True])
def test_failed_refresh_discovery_preserves_image_but_blocks_publish_and_remains_retryable(
    admin_client, database, monkeypatch, storage_enabled_after_failure
):
    from devfeed_core.image_jobs import request_image

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "image_storage_enabled", False)
    with database.begin() as session:
        article = make_article(session)
        article.image_url = "https://publisher.example/old.png"
        identifier = article.id
        job = request_image(session, identifier, refresh=True)
        job_id = job.id

    def fail(url):
        raise FeedError("unavailable", reason="fetch_http", retryable=False, status=404)

    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_tasks, "fetch_page", fail)
    image_tasks.enrich_image(str(job_id))
    monkeypatch.setattr(settings, "image_storage_enabled", storage_enabled_after_failure)
    with database.begin() as session:
        article, job = session.get(Article, identifier), session.get(ArticleImageJob, job_id)
        assert article.image_url.endswith("old.png") and job.status == "failed"
        readiness = processing_readiness(session, article)
        assert (readiness.stage, readiness.state) == ("images", "blocked")
        assert readiness.checks["images"].job_id == job_id
        assert "image_discovery_failed" in readiness.blockers
        assert (
            session.scalar(
                select(ArticleImageJob.id).where(
                    ArticleImageJob.id == job_id, retry_candidate(ArticleImageJob)
                )
            )
            == job_id
        )
        with pytest.raises(OperationConflict, match="image_discovery_failed"):
            editorial.decide_article(
                session, identifier, editorial.EditorialDecision(action="publish")
            )
    details = admin_client.get(f"/v1/admin/jobs/images/{job_id}").json()
    assert details["retryable"] is True
    body = retry_body(admin_client, identifier, "images")
    assert body["expected_job_id"] == str(job_id)
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    successor = response.json()["jobs"]["images"]
    assert successor["id"] != str(job_id) and successor["status"] == "queued"


@pytest.mark.integration
@pytest.mark.parametrize("mismatch", ["canonical", "image"])
def test_failed_refresh_discovery_cannot_retry_changed_input(database, monkeypatch, mismatch):
    from devfeed_core.image_jobs import request_image

    monkeypatch.setattr(get_settings(), "image_storage_enabled", False)
    with database.begin() as session:
        article = make_article(session)
        article.image_url = "https://publisher.example/old.png"
        job = request_image(session, article.id, refresh=True)
        job.status = "failed"
        if mismatch == "canonical":
            article.canonical_url += "-changed"
        else:
            article.image_url = "https://publisher.example/changed.png"
        session.flush()
        assert (
            session.scalar(
                select(ArticleImageJob.id).where(
                    ArticleImageJob.id == job.id, retry_candidate(ArticleImageJob)
                )
            )
            is None
        )
        assert "image_discovery_failed" not in processing_readiness(session, article).blockers


@pytest.mark.integration
def test_successful_normal_discovery_resolves_failed_refresh_for_same_missing_image(database):
    with database.begin() as session:
        article = make_article(session)
        previous = ArticleImageJob(
            article_id=article.id,
            operation="discover",
            status="failed",
            created_at=utcnow() - timedelta(minutes=1),
            storage={
                "input_url": article.canonical_url,
                "article_refresh": True,
                "previous_image_url": None,
            },
        )
        session.add(previous)
        session.add(
            ArticleImageJob(
                article_id=article.id,
                operation="discover",
                status="succeeded",
                outcome="not_found",
                storage={"input_url": article.canonical_url},
            )
        )
        session.flush()
        assert (
            session.scalar(
                select(ArticleImageJob.id).where(
                    ArticleImageJob.id == previous.id, retry_candidate(ArticleImageJob)
                )
            )
            is None
        )
        assert "image_discovery_failed" not in processing_readiness(session, article).blockers


@pytest.mark.integration
def test_completed_thumbnail_upgrade_can_refresh_and_keeps_prior_asset_until_complete(
    admin_client, database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    monkeypatch.setattr(get_settings(), "image_public_url", "https://images.example")
    source = "https://publisher.example/image.png"
    old = {
        "source_url": source,
        "version": "v1",
        "thumbnail_version": THUMBNAIL_VERSION,
        "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT,
        "hash": "old",
        "original_key": "old",
        "source_width": 640,
        "variants": [{"key": "old-640", "width": 640}],
    }
    with database.begin() as session:
        article = make_article(session)
        article.image_url, article.managed_image = source, old
        identifier = article.id
        session.add(
            ArticleImageJob(
                article_id=identifier,
                operation="store",
                image_url=source,
                status="failed",
                storage_version=THUMBNAIL_VERSION,
                storage={"thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT},
            )
        )
    body = retry_body(admin_client, identifier, "images")
    response = admin_client.post(f"/v1/admin/articles/{identifier}/pipeline/retry", json=body)
    assert response.status_code == 200, response.text
    job_id = uuid.UUID(response.json()["jobs"]["images"]["id"])
    calls, fail = [], True

    @contextmanager
    def client():
        yield object()

    def original(_store, url):
        calls.append(url)
        with database() as session:
            assert session.get(Article, identifier).managed_image == old
        return {**old, "hash": "fresh", "original_key": "fresh", "variants": []}

    def variant(_store, asset, width):
        nonlocal fail
        if width == 480 and fail:
            fail = False
            raise FeedError("retry", reason="image_transform", retryable=True)
        return {"key": f"fresh-{width}", "width": width}

    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_storage_tasks, "storage_client", client)
    monkeypatch.setattr(image_storage_tasks.ImageOriginalStore, "store", original)
    monkeypatch.setattr(image_storage_tasks.ArticleThumbnailVariantStore, "store", variant)
    image_tasks.enrich_image(str(job_id))
    with database.begin() as session:
        article, job = session.get(Article, identifier), session.get(ArticleImageJob, job_id)
        assert article.managed_image == old and job.status == "queued"
        assert job.storage["article_refresh"] is True and job.storage["variants"]
        job.available_at = utcnow()
    image_tasks.enrich_image(str(job_id))
    with database() as session:
        assert session.get(Article, identifier).managed_image["hash"] == "fresh"
        assert session.get(ArticleImageJob, job_id).status == "succeeded"
        assert len(calls) == 1
