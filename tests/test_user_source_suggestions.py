from types import SimpleNamespace

import pytest
from devfeed_core.article_automation import schedule_source_admission
from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, Source, SourceEnrichmentJob, SourceReview
from devfeed_core.source_relevance import SourceRelevance, approval_supported
from devfeed_user_api import sources
from devfeed_user_api.auth import require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from source_suggestions import identity
from sqlalchemy import func, select
from test_user_auth import complete, logout_headers
from test_user_auth import oidc_app as oidc_app

PATH = "/v1/user/sources/suggestions"
BODY = {"feed_url": "https://example.com/rss", "source_type": "publisher"}


def test_suggestions_require_real_session_and_csrf_before_work(oidc_app, monkeypatch):
    monkeypatch.setattr(sources, "limit_suggestions", lambda *_: pytest.fail("Quota before auth"))
    assert oidc_app.client.post(PATH, json=BODY).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.post(PATH, json=BODY).status_code == 403
    headers = logout_headers(oidc_app)
    headers["Origin"] = "https://attacker.example"
    assert oidc_app.client.post(PATH, json=BODY, headers=headers).status_code == 403


@pytest.fixture
def suggested_client(database):
    app = create_app()
    app.dependency_overrides[require_user] = identity
    with TestClient(app) as client:
        yield client


@pytest.mark.integration
@pytest.mark.parametrize(
    "extra",
    [
        {"enabled": True},
        {"approval_status": "approved"},
        {"publication_policy": "auto"},
        {"submitted_by": {"user_id": "other"}},
        {"source_type": "unknown"},
        {"name": " "},
        {"feed_url": "http://127.0.0.1/feed"},
        {"feed_url": "http://[::1]/feed"},
        {"feed_url": "https://u:p@example.com/feed"},
        {"feed_url": "https://example.com:8080/feed"},
        {"feed_url": "javascript:alert(1)"},
        {"feed_url": "https://example.com/" + "a" * 2048},
    ],
)
def test_suggestions_validate_and_reject_trust_fields_before_fetch(
    suggested_client, monkeypatch, extra
):
    monkeypatch.setattr(
        sources.services, "validate_source", lambda *_: pytest.fail("Fetched invalid URL")
    )
    assert suggested_client.post(PATH, json={**BODY, **extra}).status_code == 422


@pytest.mark.integration
def test_suggestion_is_private_pending_and_duplicate_cannot_mutate(
    suggested_client, database, client
):
    response = suggested_client.post(
        PATH, json={**BODY, "feed_url": " https://EXAMPLE.com:443/rss#fragment "}
    )
    assert response.status_code == 201, response.text
    assert response.json()["approval_status"] == "pending"
    assert set(response.json()) == {"id", "name", "approval_status", "created_at"}
    assert suggested_client.post(PATH, json={**BODY, "name": "Imposter"}).status_code == 409
    assert client.post("/v1/sources", json=BODY).status_code == 405
    assert client.get("/v1/sources").json() == []
    with database() as session:
        source = session.scalar(select(Source))
        assert source.enabled and source.approval_status == "pending"
        assert source.submitted_by == {
            "user_id": identity().user_id,
            "name": "Contributor",
            "verified": True,
        }
        assert source.feed_url == BODY["feed_url"] and source.name != "Imposter"
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1


@pytest.mark.integration
def test_rate_limit_is_per_user_and_fails_closed(suggested_client, monkeypatch):
    for _ in range(5):
        assert suggested_client.post(PATH, json=BODY).status_code in (201, 409)
    limited = suggested_client.post(PATH, json=BODY)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    suggested_client.app.dependency_overrides[require_user] = lambda: identity().model_copy(
        update={"user_id": "other"}
    )
    assert suggested_client.post(PATH, json=BODY).status_code == 409
    monkeypatch.setattr(
        sources,
        "get_redis",
        lambda: SimpleNamespace(eval=lambda *_: (_ for _ in ()).throw(ConnectionError())),
    )
    assert suggested_client.post(PATH, json=BODY).status_code == 503


def test_relevance_requires_complete_cited_evidence_and_strong_focus():
    sample = [
        {"index": i, "title": "Building reliable distributed software systems", "summary": ""}
        for i in range(5)
    ]
    result = SourceRelevance(
        relevance="relevant",
        confidence=0.95,
        reason="Software engineering",
        entries=[
            {"index": i, "relevance": "relevant", "evidence": sample[i]["title"]} for i in range(5)
        ],
    )
    assert approval_supported(result, sample)
    result.entries[0].relevance = "unrelated"
    assert approval_supported(result, sample)
    result.entries[1].relevance = "unrelated"
    assert not approval_supported(result, sample)
    result.entries[1].relevance = "uncertain"
    assert not approval_supported(result, sample)
    result.entries[1].relevance = "relevant"
    result.confidence = 0.6
    assert not approval_supported(result, sample)
    result.entries[1].evidence = "invented evidence about software development"
    with pytest.raises(ValueError):
        approval_supported(result, sample)
    with pytest.raises(ValueError):
        approval_supported(result.model_copy(update={"entries": result.entries[:2]}), sample)


@pytest.mark.integration
@pytest.mark.parametrize("supported", [False, True])
def test_full_automation_requires_relevance_not_just_valid_feed(
    suggested_client, database, monkeypatch, supported
):
    from devfeed_aggregator import source_tasks

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    result = suggested_client.post(PATH, json=BODY)
    assert result.status_code == 201
    assert result.json()["approval_status"] == "pending"
    with database.begin() as session:
        source = session.scalar(select(Source))
        assert source.enabled  # Default polling cannot bypass the relevance gate.
        job = session.scalar(select(SourceEnrichmentJob))
        job_id = str(job.id)
    assert schedule_source_admission(database) == 0
    monkeypatch.setattr(
        source_tasks,
        "assess_source",
        lambda *_: {
            "approval_supported": supported,
            "relevance": "relevant" if supported else "uncertain",
            "reason": "Feed evidence checked",
        },
    )
    source_tasks.enrich_source(job_id)
    with database() as session:
        source = session.scalar(select(Source))
        assert source.approval_status == ("approved" if supported else "pending")
        assert source.relevance_assessment["approval_supported"] is supported
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == int(supported)
        if supported:
            assert session.scalar(select(SourceReview.actor)) == "devfeed:source-relevance"


def test_assessor_uses_bounded_feed_evidence_and_rejects_fabrication(monkeypatch):
    from devfeed_aggregator import source_relevance
    from devfeed_core.feeds.parser import ParsedFeed
    from devfeed_core.source_relevance import feed_sample

    entry = SimpleNamespace(
        title="Engineering distributed software systems",
        summary="A guide to software infrastructure and programming.",
    )
    parsed = ParsedFeed([entry] * 20, 20, 0, title="Example")
    sample = feed_sample(parsed)
    assert len(sample) == 10
    monkeypatch.setattr(source_relevance, "validate_feed", lambda *a, **kw: parsed)
    output = {
        "relevance": "relevant",
        "confidence": 0.95,
        "reason": "Engineering focus",
        "entries": [
            {"index": i, "relevance": "relevant", "evidence": entry.title} for i in range(10)
        ],
    }
    prompts = []

    def complete(prompt, schema):
        prompts.append(prompt)
        assert "at least 20 characters" in prompt
        assert schema["properties"]["entries"]["minItems"] == 10
        assert schema["properties"]["entries"]["maxItems"] == 10
        assert schema["$defs"]["EntryRelevance"]["properties"]["index"]["enum"] == list(range(10))
        return output

    monkeypatch.setattr(
        source_relevance, "CodexClient", lambda *_: SimpleNamespace(complete=complete)
    )
    result = source_relevance.assess_source(BODY["feed_url"], "publisher")
    assert result["approval_supported"] is True
    assert "untrusted evidence" in prompts[0] and len(result["sample"]) == 10
    output["entries"][0]["evidence"] = "Fabricated quote about developer tooling"
    with pytest.raises(ValueError):
        source_relevance.assess_source(BODY["feed_url"], "publisher")
    parsed.entries[:] = [entry]
    assert source_relevance.assess_source(BODY["feed_url"], "publisher")["relevance"] == "uncertain"
    assert len(prompts) == 2  # Sparse feeds never consume an inference call.


@pytest.mark.integration
def test_source_relevance_retry_uses_safe_feedback_and_never_approves_invalid_evidence(
    suggested_client, database, monkeypatch
):
    from devfeed_aggregator import source_relevance, source_tasks
    from devfeed_core.feeds.parser import ParsedFeed
    from devfeed_core.models import utcnow
    from test_full_app_automation import full

    full(monkeypatch)
    response = suggested_client.post(PATH, json=BODY)
    assert response.status_code == 201
    with database() as session:
        job_id = session.scalar(select(SourceEnrichmentJob.id))
    title = "Engineering distributed software systems"
    parsed = ParsedFeed([SimpleNamespace(title=title, summary="")] * 3, 3, 0, title="Example")
    monkeypatch.setattr(source_relevance, "validate_feed", lambda *a, **kw: parsed)
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *a: ({}, None))
    prompts = []

    def complete(prompt, schema):
        prompts.append(prompt)
        return {
            "relevance": "relevant",
            "confidence": 0.95,
            "reason": "Engineering focus",
            "entries": [
                {
                    "index": i,
                    "relevance": "relevant",
                    "evidence": "Engineering" if len(prompts) == 1 else title,
                }
                for i in range(3)
            ],
        }

    monkeypatch.setattr(
        source_relevance, "CodexClient", lambda *_: SimpleNamespace(complete=complete)
    )
    source_tasks.enrich_source(str(job_id))
    with database.begin() as session:
        job = session.get(SourceEnrichmentJob, job_id)
        assert job.status == "queued" and job.attempts == 1
        assert job.error == "Source relevance validation failed: source_evidence_too_short"
        assert session.scalar(select(Source.approval_status)) == "pending"
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        job.available_at = utcnow()
    source_tasks.enrich_source(str(job_id))
    assert "source_evidence_too_short" in prompts[1]
    with database() as session:
        job = session.get(SourceEnrichmentJob, job_id)
        assert job.status == "succeeded" and job.attempts == 2 and job.error is None
        assert session.scalar(select(Source.approval_status)) == "approved"


@pytest.mark.integration
@pytest.mark.parametrize("during", ["reject", "failure", "disable"])
def test_relevance_cannot_override_review_failure_or_disabled_automation(
    suggested_client, database, monkeypatch, during
):
    from devfeed_aggregator import source_tasks
    from devfeed_core import services
    from devfeed_core.schemas import SourceDecision

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    assert suggested_client.post(PATH, json=BODY).status_code == 201
    with database() as session:
        job_id = str(session.scalar(select(SourceEnrichmentJob.id)))
        source_id = session.scalar(select(Source.id))

    def assess(*_):
        if during == "reject":
            with database.begin() as session:
                services.review_source(
                    session,
                    source_id,
                    SourceDecision(decision="rejected", actor="admin", note="Out of scope"),
                )
        elif during == "failure":
            raise ValueError("Invalid model evidence")
        else:
            monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "false")
            get_settings.cache_clear()
        return {"approval_supported": True, "reason": "Relevant"}

    monkeypatch.setattr(source_tasks, "assess_source", assess)
    source_tasks.enrich_source(job_id)
    with database() as session:
        source = session.get(Source, source_id)
        assert source.approval_status == ("rejected" if during == "reject" else "pending")
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0


@pytest.mark.integration
def test_concurrent_duplicate_suggestions_create_one_pending_source(
    suggested_client, database, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    original = sources.services.validate_source

    def validate(body):
        prepared = original(body)
        barrier.wait(timeout=10)
        return prepared

    monkeypatch.setattr(sources.services, "validate_source", validate)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: suggested_client.post(PATH, json=BODY).status_code, range(2))
        )
    assert sorted(results) == [201, 409]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "full_automation,ai_enabled", [(True, True), (False, True), (True, False), (False, False)]
)
def test_suggested_source_dispatch_uses_ai_lane_only_when_required(
    suggested_client, database, monkeypatch, full_automation, ai_enabled
):
    from devfeed_core.job_dispatch import dispatch_jobs
    from devfeed_core.models import utcnow

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", str(full_automation).lower())
    monkeypatch.setenv("DEVFEED_AI_ENABLED", str(ai_enabled).lower())
    get_settings.cache_clear()
    assert suggested_client.post(PATH, json=BODY).status_code == 201
    calls = []
    queue = SimpleNamespace(
        enqueue=lambda *args, **kwargs: calls.append(args) or SimpleNamespace(id="rq-test")
    )
    analysis_required = full_automation  # Full automation enables AI in Settings.
    assert dispatch_jobs(database, queue, 10, utcnow(), kind="source-enrichment") == int(
        not analysis_required
    )
    assert dispatch_jobs(
        database, queue, 10, utcnow(), kind="source-enrichment", source_analysis=True
    ) == int(analysis_required)
    assert len(calls) == 1


@pytest.mark.integration
def test_relevance_outage_stays_pending_and_reports_actual_failure(
    suggested_client, database, monkeypatch, caplog
):
    from devfeed_aggregator import source_tasks
    from devfeed_aggregator.codex_client import AnalysisError

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    assert suggested_client.post(PATH, json=BODY).status_code == 201
    with database() as session:
        job_id = session.scalar(select(SourceEnrichmentJob.id))
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *args: ({}, None))

    def unavailable(*args):
        raise AnalysisError("codex_unavailable", retry_after=600)

    monkeypatch.setattr(source_tasks, "assess_source", unavailable)
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        job = session.get(SourceEnrichmentJob, job_id)
        source = session.scalar(select(Source))
        assert source.approval_status == "pending"
        assert job.status == "queued"
        assert "codex_unavailable" in job.error
        assert (job.available_at - job.created_at).total_seconds() >= 599
    record = next(
        record for record in caplog.records if record.message == "source_enrichment_failed"
    )
    assert record.reason == "codex_unavailable"
    assert record.stage == "relevance"
    from devfeed_core.logging import TextFormatter

    message = TextFormatter("worker").format(record)
    assert "Source relevance analysis failed" in message
    assert "Codex is unreachable" in message


@pytest.mark.integration
def test_stale_ingestion_delivery_returns_to_ai_outbox_without_attempt(
    suggested_client, database, monkeypatch
):
    from devfeed_aggregator import dispatch, source_tasks
    from devfeed_core.job_dispatch import dispatch_jobs
    from devfeed_core.models import utcnow

    assert suggested_client.post(PATH, json=BODY).status_code == 201
    with database() as session:
        job_id = session.scalar(select(SourceEnrichmentJob.id))
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        source_tasks, "get_current_job", lambda: SimpleNamespace(origin="ingestion")
    )
    monkeypatch.setattr(
        source_tasks, "lookup_profile", lambda *_: pytest.fail("Wrong worker fetched feed")
    )
    monkeypatch.setattr(
        source_tasks, "assess_source", lambda *_: pytest.fail("Wrong worker called Codex")
    )
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        job = session.get(SourceEnrichmentJob, job_id)
        assert job.status == "queued" and job.attempts == 0
        assert job.lease_token is None and job.dispatched_at is None
    queues = []
    deliveries = []

    def queue(name="ingestion"):
        queues.append(name)
        return SimpleNamespace(
            enqueue=lambda *args, **kwargs: deliveries.append(args) or SimpleNamespace(id="rq-id"),
            connection=SimpleNamespace(close=lambda: None),
        )

    monkeypatch.setattr(dispatch, "get_queue", queue)
    dispatch.dispatch_now(job_id, kind="source-enrichment")
    assert queues == ["analysis"]
    assert len(deliveries) == 1
    assert dispatch_jobs(database, queue(), 1, utcnow(), kind="source-enrichment") == 0


@pytest.mark.parametrize("path", [PATH, PATH + "/preview"])
def test_source_preview_requires_session_and_csrf(oidc_app, path):
    assert oidc_app.client.post(path, json=BODY).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.post(path, json=BODY).status_code == 403


@pytest.mark.integration
def test_preview_returns_feed_name_without_saving_or_consuming_submission_quota(
    suggested_client, database
):
    for _ in range(20):
        response = suggested_client.post(PATH + "/preview", json=BODY)
        assert response.status_code == 200
        assert set(response.json()) == {"name"} and response.json()["name"]
    limited = suggested_client.post(PATH + "/preview", json=BODY)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 0
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0
    assert suggested_client.post(PATH, json=BODY).status_code == 201


@pytest.mark.integration
def test_preview_rejects_private_urls_and_unreadable_feeds(suggested_client, monkeypatch):
    from devfeed_core.feeds.fetcher import FeedError
    from devfeed_core.feeds.validation import FeedValidationError

    assert (
        suggested_client.post(
            PATH + "/preview", json={**BODY, "feed_url": "http://127.0.0.1/feed"}
        ).status_code
        == 422
    )

    def invalid(*args):
        raise FeedValidationError(FeedError("Unreadable feed", reason="unreadable_feed"))

    monkeypatch.setattr(sources.services, "validate_source", invalid)
    assert suggested_client.post(PATH + "/preview", json=BODY).status_code == 422


@pytest.mark.integration
@pytest.mark.parametrize("during_lookup", [False, True])
def test_source_deletion_removes_active_profile_jobs_and_discards_late_results(
    suggested_client, admin_client, database, monkeypatch, during_lookup
):
    import uuid

    from devfeed_aggregator import source_tasks

    source_id = uuid.UUID(suggested_client.post(PATH, json=BODY).json()["id"])
    with database() as session:
        job_id = session.scalar(select(SourceEnrichmentJob.id))

    def delete_source():
        response = admin_client.delete(f"/v1/admin/sources/{source_id}")
        assert response.status_code == 204, response.text

    def lookup(*args):
        assert during_lookup
        delete_source()
        return {"description": "Late profile data"}, None

    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    if not during_lookup:
        delete_source()
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        assert session.get(Source, source_id) is None
        assert session.get(SourceEnrichmentJob, job_id) is None
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
    # An already-published duplicate delivery is harmless after deletion.
    source_tasks.enrich_source(str(job_id))
