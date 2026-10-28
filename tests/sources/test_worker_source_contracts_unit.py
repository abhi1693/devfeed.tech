"""Source worker transport and orchestration; repository results are preloaded facts."""

import copy
import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import source_relevance, source_tasks
from devfeed_core import source_lifecycle
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.job_lifecycle import start_job
from devfeed_core.source_enrichment import source_identity
from devfeed_core.source_profiles import PROFILE_FIELDS
from devfeed_core.source_relevance import VERSION

from tests.support.source_readiness import NOW, configuration, profile, source

FEED = "https://publisher.example/news/feed.xml"
WEBSITE = "https://publisher.example/"
XML = b"""<rss version="2.0"><channel><title>Engineering</title>
<description>Engineering articles from the feed.</description>
<link>https://publisher.example/</link><language>en</language>
<item><title>Understanding database transactions</title>
<link>https://publisher.example/transactions</link><guid>transactions</guid>
<pubDate>Thu, 22 Oct 2026 08:00:00 GMT</pubDate>
<description>Database transactions preserve consistency during concurrent updates.</description>
</item></channel></rss>"""
HTML = b"""<html lang="fr"><head><meta name="description" content="Website description">
<link rel="icon" href="/icon.png"><meta property="og:image" content="/cover.png">
</head><body>Engineering publisher</body></html>"""


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(source_tasks, "utcnow", lambda: NOW)
    feed = Mock(return_value=FetchResult(200, XML, FEED))
    page = Mock(return_value=FetchResult(200, HTML, WEBSITE, content_type="text/html"))
    monkeypatch.setattr(source_tasks, "fetch_feed", feed)
    monkeypatch.setattr(source_tasks, "fetch_source_page", page)
    return feed, page


def test_profile_combines_real_feed_and_html_parsers_preserving_feed_precedence(transport):
    feed, page = transport
    values, error = source_tasks.lookup_profile(FEED, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert error is None
    feed.assert_called_once_with(FEED)
    page.assert_called_once_with(WEBSITE)
    assert values["description"] == "Engineering articles from the feed."
    assert values["language"] == "en" and values["website_url"] == WEBSITE
    assert (
        values["logo_url"] == WEBSITE + "icon.png" and values["image_url"] == WEBSITE + "cover.png"
    )
    assert values["_sample"] == [
        {
            "index": 0,
            "title": "Understanding database transactions",
            "summary": "Database transactions preserve consistency during concurrent updates.",
        }
    ]


def test_profile_uses_saved_website_for_completion_and_skips_transport_when_already_complete(
    transport,
):
    _, page = transport
    existing = dict.fromkeys(PROFILE_FIELDS)
    existing["website_url"] = "https://publisher.example/about"
    source_tasks.lookup_profile(FEED, "publisher", existing)
    page.assert_called_once_with(existing["website_url"])
    page.reset_mock()
    source_tasks.lookup_profile(
        FEED,
        "publisher",
        {
            "description": "Reviewed engineering publisher.",
            "website_url": WEBSITE,
            "logo_url": WEBSITE + "icon.png",
            "image_url": WEBSITE + "cover.png",
            "language": "en",
        },
    )
    page.assert_not_called()


def test_profile_missing_website_uses_redirected_feed_origin(transport):
    feed, page = transport
    feed.return_value = FetchResult(
        200,
        XML.replace(b"<link>https://publisher.example/</link>", b""),
        "https://canonical.example/news/feed.xml?x=1",
    )
    source_tasks.lookup_profile(FEED, "publisher", dict.fromkeys(PROFILE_FIELDS))
    page.assert_called_once_with("https://canonical.example/")


def test_profile_website_feed_mistake_retries_root_with_guarded_page_transport(transport):
    _, page = transport
    page.side_effect = [
        FeedError("RSS response", reason="unsupported_content_type"),
        FetchResult(200, HTML, WEBSITE, content_type="text/html"),
    ]
    existing = dict.fromkeys(PROFILE_FIELDS)
    existing["website_url"] = FEED
    values, error = source_tasks.lookup_profile(FEED, "publisher", existing)
    assert error is None and values["logo_url"] == WEBSITE + "icon.png"
    assert [call.args[0] for call in page.call_args_list] == [FEED, WEBSITE]


def test_feed_transport_failure_identifies_feed_resource_and_keeps_retry_metadata(transport):
    feed, page = transport
    error = FeedError(
        "unavailable", reason="http_error", retryable=True, status=503, retry_after=120
    )
    feed.side_effect = error
    with pytest.raises(FeedError) as raised:
        source_tasks.lookup_profile(FEED, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert raised.value is error and error.resource == "Feed"
    assert error.retryable and error.status == 503 and error.retry_after == 120
    page.assert_not_called()


def test_metadata_lookup_requires_complete_feed_instead_of_parsing_not_modified_body(transport):
    feed, page = transport
    feed.return_value = FetchResult(304, b"", FEED)
    with pytest.raises(FeedError) as raised:
        source_tasks.lookup_profile(FEED, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert raised.value.reason == "missing_body"
    page.assert_not_called()


def test_website_failure_returns_feed_profile_and_sample_with_actionable_resource(transport):
    _, page = transport
    error = FeedError("requires challenge", reason="browser_challenge", retryable=True, status=403)
    page.side_effect = error
    values, reported = source_tasks.lookup_profile(FEED, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert reported is error and reported.resource == "Source website"
    assert values["description"] == "Engineering articles from the feed."
    assert values["_sample"][0]["title"] == "Understanding database transactions"
    assert values["logo_url"] is None


@pytest.fixture
def runtime(monkeypatch):
    current = source()
    job = profile(current, status="running", created_at=NOW, available_at=NOW, changed_fields=[])
    session = Mock()
    # This is the primary-key source lookup boundary, never a SQL predicate evaluator.
    session.scalar.return_value = current
    factory = Mock(return_value=nullcontext(session))
    factory.begin.return_value = nullcontext(session)
    monkeypatch.setattr(source_tasks, "session_factory", lambda: factory)
    claim, owned = Mock(return_value=(job, current)), Mock(return_value=job)
    monkeypatch.setattr(source_tasks, "claim_enrichment", claim)
    monkeypatch.setattr(source_tasks, "owned_job", owned)
    monkeypatch.setattr(
        source_tasks, "get_current_job", lambda: SimpleNamespace(origin="source-enrichment")
    )
    monkeypatch.setattr(source_tasks, "utcnow", lambda: NOW)
    lookup = Mock(return_value=({}, None))
    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    monkeypatch.setattr(source_tasks, "defer_to_solver", Mock(return_value=False))
    return SimpleNamespace(
        source=current,
        job=job,
        session=session,
        claim=claim,
        owned=owned,
        lookup=lookup,
        reconcile=reconcile,
    )


def test_missing_feed_url_keeps_profile_retry_and_releases_lease(runtime: SimpleNamespace) -> None:
    runtime.source.feed_url = None
    runtime.job.result["input"] = source_identity(runtime.source)

    source_tasks.enrich_source(str(runtime.job.id))

    runtime.lookup.assert_not_called()
    assert runtime.job.status == "queued" and runtime.job.available_at > NOW
    assert runtime.job.error == "Source enrichment error: TypeError"
    assert runtime.job.lease_token is runtime.job.lease_until is None


def relevance(runtime, monkeypatch):
    current = runtime.source
    prior = profile(current, created_at=NOW - timedelta(minutes=2))
    sample = [
        {
            "index": i,
            "title": f"Database transaction lesson {i}",
            "summary": "Transactions preserve consistency during concurrent updates.",
        }
        for i in range(3)
    ]
    digest = hashlib.sha256(
        json.dumps(sample, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    prior.result = {**prior.result, "sample": sample, "sample_hash": digest}
    runtime.job.operation = "relevance"
    runtime.job.result = {
        "input": source_identity(current),
        "sample": sample,
        "sample_hash": digest,
        "profile_job_id": str(prior.id),
        "policy_version": VERSION,
    }
    runtime.job.error = None
    client = SimpleNamespace(
        model="assessment-model",
        complete=Mock(
            return_value={
                "relevance": "uncertain",
                "confidence": 0.5,
                "reason": "The examples do not establish the publisher's overall editorial focus.",
                "entries": [
                    {"index": i, "relevance": "uncertain", "evidence": "none"} for i in range(3)
                ],
            }
        ),
    )
    monkeypatch.setattr(source_relevance, "CodexClient", lambda _: client)
    monkeypatch.setattr(source_relevance, "get_settings", lambda: configuration(ai_enabled=True))
    monkeypatch.setattr(source_relevance, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        source_tasks, "get_current_job", lambda: SimpleNamespace(origin="source-analysis")
    )
    runtime.client, runtime.sample = client, sample
    return runtime


def test_obsolete_background_relevance_delivery_is_rerouted_without_model_call_or_attempt_cost(
    runtime, monkeypatch
):
    relevance(runtime, monkeypatch)
    monkeypatch.setattr(
        source_tasks, "get_current_job", lambda: SimpleNamespace(origin="ingestion")
    )
    before = copy.deepcopy(runtime.source.relevance_assessment)
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.job.status == "queued" and runtime.job.attempts == 0
    assert runtime.job.available_at == NOW and runtime.job.dispatched_at is None
    assert runtime.job.lease_token is runtime.job.lease_until is None
    assert (
        runtime.source.relevance_assessment == before
        and runtime.source.approval_status == "pending"
    )
    runtime.client.complete.assert_not_called()


def test_real_relevance_validation_retries_with_safe_feedback_instead_of_storing_bad_output(
    runtime, monkeypatch
):
    relevance(runtime, monkeypatch)
    runtime.client.complete.return_value["entries"][0]["evidence"] = "invented-reference"
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.job.status == "queued" and runtime.source.relevance_assessment == {}
    assert runtime.job.error == "Source relevance validation failed: source_evidence_not_in_sample"
    assert runtime.source.metadata_error is None

    # A fresh repository lease represents the next delivery; validation feedback is persisted above.
    start_job(runtime.job, NOW + timedelta(seconds=30), 300)
    monkeypatch.setattr(source_tasks, "utcnow", lambda: NOW + timedelta(seconds=30))
    monkeypatch.setattr(source_relevance, "utcnow", lambda: NOW + timedelta(seconds=30))
    runtime.client.complete.return_value["entries"][0]["evidence"] = "none"
    source_tasks.enrich_source(str(runtime.job.id))
    prompt = runtime.client.complete.call_args.args[0]
    assert '"code": "source_evidence_not_in_sample"' in prompt
    assert runtime.client.quality_failure
    assert runtime.job.status == "succeeded" and runtime.job.attempts == 2
    assert not runtime.source.relevance_assessment["approval_supported"]
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    assert runtime.job.result["output"]["profile_job_id"] == runtime.job.result["profile_job_id"]
    assert runtime.job.result["output"]["sample_hash"] == runtime.job.result["sample_hash"]


@pytest.mark.parametrize(
    "failure,phrase",
    [
        (
            FeedError("upstream", reason="http_error", status=403, resource="Feed"),
            "Feed rejected the request (HTTP 403).",
        ),
        (
            FeedError(
                "upstream", reason="browser_challenge", status=403, resource="Source website"
            ),
            "Source website requires browser verification (HTTP 403).",
        ),
        (
            FeedError("upstream", reason="unsupported_content_type"),
            "Publisher did not return an HTML page.",
        ),
        (
            FeedError("upstream", reason="solver_unavailable"),
            "Publisher solver service is unavailable.",
        ),
        (
            FeedError("upstream", reason="solver_busy", retryable=True),
            "Publisher solver service is busy.",
        ),
        (
            FeedError(
                "upstream",
                reason="response_too_large",
                limit_bytes=2_000_000,
                limit_setting="DEVFEED_FEED_MAX_BYTES",
            ),
            "Feed exceeds the configured 2,000,000-byte download limit (DEVFEED_FEED_MAX_BYTES).",
        ),
        (
            FeedError("upstream", reason="response_too_large", limit_bytes=1000),
            "Response exceeds the configured 1,000-byte download limit.",
        ),
    ],
)
def test_profile_failure_retains_partial_metadata_and_explains_transport_or_limit(
    runtime, failure, phrase
):
    runtime.lookup.return_value = ({"description": "Retrieved engineering description."}, failure)
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.source.description == "Retrieved engineering description."
    assert runtime.job.error.endswith(phrase) and runtime.source.metadata_error == runtime.job.error
    assert runtime.job.status == ("queued" if failure.retryable else "failed")
    assert runtime.job.lease_token is None
    assert runtime.job.result["input"] == source_identity(runtime.source)
    runtime.reconcile.assert_not_called()


@pytest.mark.parametrize("missing", ["source", "ownership"])
def test_removed_source_or_lease_rejection_does_not_publish_external_results(runtime, missing):
    if missing == "source":
        runtime.session.scalar.return_value = None
    else:
        runtime.owned.return_value = None
    runtime.lookup.return_value = ({"description": "New profile description."}, None)
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.source.description is None and runtime.job.status == "running"
    runtime.reconcile.assert_not_called()


def test_editorial_rejection_during_fetch_wins_over_successful_profile_result(runtime):
    def fetched(*args):
        runtime.source.approval_status = "rejected"
        runtime.source.reviewed_at, runtime.source.reviewed_by = NOW, "editor"
        runtime.source.review_note = "Outside the developer audience."
        return {"description": "Newly retrieved profile."}, None

    runtime.lookup.side_effect = fetched
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.source.approval_status == "rejected" and runtime.source.description is None
    assert (
        runtime.job.status == "failed"
        and runtime.job.error == "Source was rejected during enrichment"
    )
    runtime.reconcile.assert_not_called()


@pytest.mark.parametrize("approved", [False, True])
def test_changed_source_identity_invalidates_old_results_and_only_reconciles_pending_intake(
    runtime, approved
):
    if approved:
        runtime.source.approval_status, runtime.source.enabled = "approved", True
        runtime.source.reviewed_at, runtime.source.reviewed_by = NOW - timedelta(days=1), "editor"

    def fetched(*args):
        runtime.source.feed_url = "https://publisher.example/replaced-feed.xml"
        return {"description": "Old feed description."}, None

    runtime.lookup.side_effect = fetched
    source_tasks.enrich_source(str(runtime.job.id))
    assert runtime.source.description is None
    assert runtime.job.status == "failed" and runtime.job.error == "Source input changed"
    if approved:
        runtime.reconcile.assert_not_called()
    else:
        runtime.reconcile.assert_called_once_with(runtime.session, runtime.source, automatic=True)
