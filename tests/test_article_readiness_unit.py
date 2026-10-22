"""Publication readiness uses current evidence and preserves explicit recovery gates."""

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.article_lifecycle import (
    derive_publication_readiness,
    processing_readiness,
    publication_readiness,
    reconcile_processing,
)
from devfeed_core.config import get_settings
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.models import ArticleImageJob
from test_article_lifecycle import facts as completed_facts

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def processing_case(monkeypatch):
    article, facts = completed_facts()
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    article.discovered_at = NOW - timedelta(hours=1)
    article.automation_next_check_at = NOW + timedelta(minutes=5)
    article.origins[0].source.feed_url = "https://publisher.example/feed"
    article.origins[0].source.enabled = True
    article.origins[0].entry_key = str(article.id)
    article.origins[0].original_url = article.canonical_url
    facts.update(settings=settings, now=NOW, content=None, discovery=None, storage=None)
    for name in ("analysis", "enrichment"):
        job = facts[name]
        job.available_at = job.created_at = NOW - timedelta(minutes=3)
        job.finished_at = NOW - timedelta(minutes=2)
    return article, facts


def image_job(
    article,
    *,
    operation="store",
    status="succeeded",
    created_at=NOW - timedelta(minutes=1),
    **values,
):
    terminal = status in {"succeeded", "failed"}
    details = {
        "finished_at": created_at + timedelta(seconds=1) if terminal else None,
        "outcome": ("stored" if operation == "store" else "not_found")
        if status == "succeeded"
        else None,
        **values,
    }
    return ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation=operation,
        status=status,
        image_url=article.image_url if operation == "store" else None,
        storage={"input_url": article.canonical_url},
        created_at=created_at,
        available_at=created_at,
        attempts=1,
        **details,
    )


@pytest.mark.parametrize(
    "status,attempts,delay,expected_state,check_status",
    [
        ("queued", 0, 60, "waiting", "queued"),
        ("queued", 1, 60, "retry_wait", "retry_wait"),
        ("queued", 1, -1, "waiting", "queued"),
        ("running", 1, -1, "running", "running"),
        ("failed", 3, -1, "blocked", "failed"),
    ],
)
@pytest.mark.parametrize("step", ["enrichment", "analysis"])
def test_live_work_and_retry_delays_remain_visible_until_completion(
    processing_case, status, attempts, delay, expected_state, check_status, step
):
    article, facts = processing_case
    job = facts[step]
    job.id = uuid.uuid4()  # A fresh run leaves previous applied analysis provenance intact.
    job.status, job.attempts, job.outcome, job.finished_at = status, attempts, None, None
    job.available_at = NOW + timedelta(seconds=delay)
    if status == "running":
        job.lease_token, job.lease_until = uuid.uuid4(), NOW + timedelta(minutes=3)
    if status == "failed":
        job.finished_at, job.error = NOW, "provider_unavailable"

    result = derive_publication_readiness(article, **facts)

    assert (result.stage, result.state) == (step, expected_state)
    assert result.checks[step].status == check_status
    assert result.checks[step].job_id == job.id
    assert result.checks[step].available_at == job.available_at
    assert f"{step}_{'failed' if status == 'failed' else 'pending'}" in result.blockers


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_missing_analysis_never_grants_publication_even_when_ai_is_disabled(
    processing_case, monkeypatch, ai_enabled
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "ai_enabled", ai_enabled)
    facts["analysis"] = None

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "analysis" and "current_analysis_required" in result.blockers
    assert result.checks["analysis"].status == ("missing" if ai_enabled else "disabled")
    assert ("ai_not_configured" in result.blockers) is not ai_enabled


def test_page_paywall_and_unreadable_text_are_independent_publication_blockers(processing_case):
    article, facts = processing_case
    article.summary = "12345 ---"
    facts["enrichment"].result = {**facts["enrichment"].result, "paywalled": True}

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "enrichment" and result.state == "blocked"
    assert {"paywalled_content", "insufficient_source_text"} <= set(result.blockers)
    assert result.checks["enrichment"].status == "complete"


@pytest.mark.parametrize("outcome", ["metadata_only", "not_found"])
def test_completed_page_lookup_can_use_rss_evidence_and_reader_image_fallback(
    processing_case, outcome
):
    article, facts = processing_case
    facts["enrichment"].outcome = outcome

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "ready" and not result.blockers
    assert result.checks["enrichment"].status == "complete"
    assert result.checks["images"].status == "unavailable"


def test_superseded_extraction_cannot_satisfy_current_page_processing(processing_case):
    article, facts = processing_case
    facts["enrichment"].outcome = "superseded"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "enrichment" and result.state == "blocked"
    assert result.checks["enrichment"].status == "stale"
    assert "current_enrichment_required" in result.blockers


def test_manual_metadata_preserves_current_generation_identity_without_forcing_reanalysis(
    processing_case,
):
    article, facts = processing_case
    original = article.classification_provenance
    article.classification_provenance = {
        "origin": "manual",
        "developer_relevance": "relevant",
        "page_kind": "article",
        "ai_generation": original,
    }

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "ready" and result.blockers == []
    assert result.checks["analysis"].status == "complete"


def test_changed_relevant_catalog_requires_fresh_analysis(processing_case):
    article, facts = processing_case
    facts["taxonomy"] = copy.deepcopy(facts["taxonomy"])
    facts["taxonomy"]["topics"][0]["name"] = "Angular application routing"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "analysis" and result.state == "blocked"
    assert result.checks["analysis"].status == "stale"
    assert "current_catalog_required" in result.blockers


@pytest.mark.parametrize("missing", ["approval", "language", "source"])
def test_finished_processing_does_not_override_editorial_or_source_authority(
    processing_case, missing
):
    article, facts = processing_case
    if missing == "approval":
        article.review_status = "pending"
    elif missing == "language":
        article.language = None
    else:
        article.origins[0].source.approval_status = "rejected"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "review"
    assert result.state == ("waiting" if missing == "approval" else "blocked")
    assert {
        "approval": "not_approved",
        "language": "unknown_language",
        "source": "no_approved_source",
    }[missing] in result.blockers
    assert result.checks["analysis"].status == "complete"


@pytest.mark.parametrize(
    "asset_change",
    [
        "wrong-source",
        "old-version",
        "empty-variants",
        "invalid-variant",
        "missing-key",
        "zero-width",
    ],
)
def test_unusable_stored_image_metadata_requires_storage_again(
    processing_case, monkeypatch, asset_change
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "image_storage_enabled", True)
    monkeypatch.setattr(facts["settings"], "image_public_url", "https://images.example")
    article.image_url = "https://publisher.example/current.png"
    article.managed_image = {
        "source_url": article.image_url,
        "version": IMAGE_VERSION,
        "variants": [{"key": "current.webp", "width": 640}],
    }
    facts["storage"] = image_job(article)
    if asset_change == "wrong-source":
        article.managed_image["source_url"] = "https://publisher.example/old.png"
    elif asset_change == "old-version":
        article.managed_image["version"] = "retired-transform-version"
    elif asset_change == "empty-variants":
        article.managed_image["variants"] = []
    elif asset_change == "invalid-variant":
        article.managed_image["variants"] = [None]
    elif asset_change == "missing-key":
        article.managed_image["variants"] = [{"width": 640}]
    else:
        article.managed_image["variants"] = [{"key": "current.webp", "width": 0}]

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "images" and result.state == "blocked"
    assert result.checks["images"].status == "stale"
    assert "image_storage_required" in result.blockers


@pytest.mark.parametrize("newer_store", [False, True])
def test_explicit_image_refresh_failure_is_visible_until_a_later_storage_success(
    processing_case, monkeypatch, newer_store
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "image_storage_enabled", True)
    monkeypatch.setattr(facts["settings"], "image_public_url", "https://images.example")
    article.image_url = "https://publisher.example/current.png"
    article.managed_image = {
        "source_url": article.image_url,
        "version": IMAGE_VERSION,
        "variants": [{"key": "current.webp", "width": 640}],
    }
    facts["storage"] = image_job(
        article,
        created_at=NOW - timedelta(seconds=30) if newer_store else NOW - timedelta(minutes=2),
    )
    refresh = facts["discovery"] = image_job(article, operation="discover", status="failed")
    refresh.storage = {
        "input_url": article.canonical_url,
        "article_refresh": True,
        "previous_image_url": article.image_url,
    }
    refresh.error = "page_fetch_unavailable"

    result = derive_publication_readiness(article, **facts)

    if newer_store:
        assert result.stage == "ready" and not result.blockers
        assert result.checks["images"].job_id == facts["storage"].id
    else:
        assert result.stage == "images" and result.state == "blocked"
        assert result.checks["images"].job_id == refresh.id
        assert "image_discovery_failed" in result.blockers


@pytest.mark.parametrize("terminal", ["published", "rejected"])
def test_terminal_history_needs_no_evidence_queries_or_new_processing(processing_case, terminal):
    article, _ = processing_case
    if terminal == "published":
        article.publication_status = "published"
        article.published_to_feed_at = NOW
    else:
        article.review_status = "rejected"

    # Old completed evidence may have been pruned; a terminal view needs no session.
    for derive in (processing_readiness, publication_readiness, reconcile_processing):
        result = derive(None, article)
        assert (result.stage, result.state) == (terminal, "complete")
        assert result.blockers == [] and result.checks == {}
