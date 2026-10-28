"""Offline board composition and readiness caching preserve operator admission.

Pages and current job rows are scoped repository facts. These tests exercise
Python ordering, document composition and cache identity, not SQL page selection.
"""

import copy
import hashlib
import uuid
from collections import OrderedDict
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import analysis
from devfeed_core import analytics_article_cards as pipeline
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import ArticleContent
from devfeed_core.urls import fingerprint

from tests.support.article_readiness import image_job
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW

FACT_NAMES = ("content", "enrichment", "analysis", "discovery", "storage")


def history(base, label, *, status="ready", with_content=False, source_name=None):
    article, facts = copy.deepcopy(base)
    article.id = uuid.uuid4()
    article.canonical_url = f"https://publisher.example/{label}"
    article.url_hash = fingerprint(article.canonical_url)
    article.discovered_at = NOW - timedelta(hours=1)
    for origin in article.origins:
        origin.article_id = article.id
    for link in article.topic_links:
        link.article_id = article.id
    article.origins[0].entry_key = label
    article.origins[0].original_url = article.canonical_url
    if source_name is not None:
        article.origins[0].source.id = uuid.uuid4()
        article.origins[0].source_id = article.origins[0].source.id
        article.origins[0].source.name = source_name
    for name in ("analysis", "enrichment"):
        facts[name].id, facts[name].article_id = uuid.uuid4(), article.id
    facts["enrichment"].result = {"input_url": article.canonical_url, "image_url": None}
    if with_content:
        text = (
            "Angular router guards allow developers to control navigation "
            "with dependency injection."
        )
        facts["content"] = ArticleContent(
            article_id=article.id,
            url=article.canonical_url,
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            method="trafilatura",
            retrieved_at=NOW - timedelta(minutes=4),
        )
    snapshot = analysis.source_snapshot(article, facts["content"])
    candidates = analysis.analysis_candidates(facts["taxonomy"], snapshot)
    current = facts["analysis"]
    current.input_snapshot, current.input_hash = snapshot, analysis.snapshot_hash(snapshot)
    current.catalog_snapshot, current.catalog_hash = candidates, analysis.snapshot_hash(candidates)
    article.classification_provenance = {
        **article.classification_provenance,
        "analysis_id": str(current.id),
        "input_hash": current.input_hash,
    }
    if status != "ready":
        current.id = uuid.uuid4()  # A newer run leaves the previously applied receipt intact.
        current.status = (
            "queued"
            if status in {"waiting", "retry_wait"}
            else "failed"
            if status == "blocked"
            else "running"
        )
        current.outcome, current.finished_at, current.result = None, None, {}
        current.created_at = NOW - timedelta(minutes=1)
        current.available_at = (
            NOW + timedelta(minutes=2) if current.status == "queued" else NOW - timedelta(minutes=1)
        )
        current.attempts = 0 if status == "waiting" else 3 if status == "blocked" else 1
        current.lease_token, current.lease_until = (
            (uuid.uuid4(), NOW + timedelta(minutes=4))
            if current.status == "running"
            else (None, None)
        )
        current.error = "provider_unavailable" if status in {"blocked", "retry_wait"} else None
        if current.status == "failed":
            current.finished_at = NOW
    return article, facts


def fact_rows(histories):
    return {
        name: {article.id: facts[name] for article, facts in histories if facts[name] is not None}
        for name in FACT_NAMES
    }


def card(value):
    article, facts = value
    readiness = derive_publication_readiness(article, **facts)
    return pipeline._card(article, readiness, facts, taxonomy=facts["taxonomy"])


@pytest.fixture
def isolated_cache(monkeypatch):
    cache = OrderedDict()
    monkeypatch.setattr(pipeline, "_readiness_cache", cache)
    return cache


@pytest.mark.parametrize("lightweight", [False, True])
def test_current_fact_batch_keeps_private_evidence_separate_and_attaches_precomputed_retry_metadata(
    processing_case, lightweight
):
    article, facts = history(processing_case, "fact-batch", with_content=True, status="blocked")
    discovery = image_job(article, operation="discover", status="succeeded")
    article.image_url = "https://publisher.example/hero.png"
    storage = image_job(article, operation="store", status="failed")
    storage.attempts = 3
    storage.error = "origin_unavailable"
    rows = iter(
        [
            [(facts["enrichment"], False)],
            [(facts["analysis"], True, "topics")] if lightweight else [(facts["analysis"], True)],
            [(discovery, False)],
            [(storage, True)],
        ]
    )
    session = SimpleNamespace(scalars=lambda _: [facts["content"]], execute=lambda _: next(rows))
    result = pipeline._batch_facts(session, [article], lightweight=lightweight)
    assert result["content"] == {article.id: facts["content"]}
    assert result["analysis"][article.id] is facts["analysis"]
    assert result["analysis"][article.id]._pipeline_retryable
    assert result["discovery"][article.id] is discovery and result["storage"][article.id] is storage
    assert storage._pipeline_retryable and not discovery._pipeline_retryable
    assert getattr(facts["analysis"], "_pipeline_current_step", None) == (
        "topics" if lightweight else None
    )
    assert facts["content"].text.startswith("Angular router")


@pytest.mark.parametrize("with_content", [False, True])
def test_readiness_cache_reuses_unchanged_evidence_then_rechecks_changed_source_authority(
    monkeypatch, processing_case, isolated_cache, with_content
):
    article, facts = history(processing_case, "cache", with_content=with_content)
    supplied = fact_rows([(article, facts)])
    monkeypatch.setattr(pipeline, "_batch_facts", lambda *_args, **_kwargs: supplied)
    session = Mock()
    session.scalars.side_effect = lambda statement: (
        [facts["content"]]
        if statement.column_descriptions[0]["entity"] is ArticleContent
        else [facts["analysis"]]
    )
    options = dict(taxonomy=facts["taxonomy"], settings=facts["settings"], now=NOW)
    _, first = pipeline._batch_readiness(session, [article], **options)
    assert first[article.id].stage == "ready" and first[article.id].blockers == []
    hydration_count = session.scalars.call_count
    _, second = pipeline._batch_readiness(session, [article], **options)
    assert second[article.id] is first[article.id]
    assert session.scalars.call_count == hydration_count
    source = article.origins[0].source
    source.approval_status, source.enabled = "rejected", False
    source.reviewed_at, source.reviewed_by, source.review_note = (
        NOW,
        "editor",
        "Source no longer meets editorial policy.",
    )
    _, changed = pipeline._batch_readiness(session, [article], **options)
    assert changed[article.id] is not first[article.id]
    assert (
        changed[article.id].stage != "ready"
        and "no_approved_source" in changed[article.id].blockers
    )
    assert session.scalars.call_count > hydration_count


def test_readiness_cache_evicts_least_recently_used_documents_without_changing_admission(
    monkeypatch, processing_case, isolated_cache
):
    histories = [history(processing_case, f"bounded-{index}") for index in range(3)]
    monkeypatch.setattr(pipeline, "_CACHE_SIZE", 2)
    monkeypatch.setattr(
        pipeline,
        "_batch_facts",
        lambda _session, articles, **_: fact_rows(
            [value for value in histories if value[0].id in {article.id for article in articles}]
        ),
    )
    session = Mock()
    session.scalars.return_value = [value[1]["analysis"] for value in histories]

    def evaluate(index):
        article, facts = histories[index]
        return pipeline._batch_readiness(
            session, [article], taxonomy=facts["taxonomy"], settings=facts["settings"], now=NOW
        )[1][article.id]

    first, second = evaluate(0), evaluate(1)
    assert evaluate(0) is first  # Touch the first entry before admitting a third.
    assert evaluate(2).stage == "ready" and len(isolated_cache) == 2
    assert {key[0] for key in isolated_cache} == {histories[0][0].id, histories[2][0].id}
    assert evaluate(1).stage == "ready" and evaluate(1) is not second
