"""Automatic source rejection requires affirmative, validated out-of-scope evidence."""

from types import SimpleNamespace

import pytest
from devfeed_aggregator import source_relevance, source_tasks
from devfeed_core import services
from devfeed_core.config import get_settings
from devfeed_core.feeds.parser import ParsedFeed
from devfeed_core.models import IngestionJob, Source, SourceEnrichmentJob, SourceReview
from devfeed_core.schemas import SourceDecision
from devfeed_core.source_enrichment import request_enrichment
from devfeed_core.source_relevance import SourceRelevance, rejection_supported
from sqlalchemy import func, select

TITLE = "Travel destinations and holiday restaurant recommendations"


def assessment(size=10, unrelated=8, confidence=0.9, verdict="unrelated"):
    sample = [{"index": i, "title": TITLE, "summary": ""} for i in range(size)]
    result = SourceRelevance(
        relevance=verdict,
        confidence=confidence,
        reason="The sampled feed focuses on travel and dining, outside software development.",
        entries=[
            {
                "index": i,
                "relevance": "unrelated" if i < unrelated else "uncertain",
                "evidence": TITLE if i < unrelated else "",
            }
            for i in range(size)
        ],
    )
    return result, sample


@pytest.mark.parametrize(
    "size,unrelated,confidence,verdict,expected",
    [
        (10, 8, 0.9, "unrelated", True),
        (3, 3, 0.95, "unrelated", True),
        (2, 2, 0.99, "unrelated", False),
        (10, 7, 0.99, "unrelated", False),
        (10, 10, 0.89, "unrelated", False),
        (10, 10, 0.99, "uncertain", False),
        (10, 10, 0.99, "relevant", False),
    ],
)
def test_rejection_thresholds(size, unrelated, confidence, verdict, expected):
    result, sample = assessment(size, unrelated, confidence, verdict)
    assert rejection_supported(result, sample) is expected


@pytest.mark.parametrize("invalid", ["short", "fabricated", "missing", "duplicate"])
def test_invalid_rejection_evidence_is_not_a_decision(invalid):
    result, sample = assessment()
    if invalid == "short":
        result.entries[0].evidence = "Travel"
    elif invalid == "fabricated":
        result.entries[0].evidence = "An invented quote that never appeared in the feed"
    elif invalid == "missing":
        result.entries.pop()
    else:
        result.entries[0].index = 1
    with pytest.raises(ValueError):
        rejection_supported(result, sample)


@pytest.fixture
def review_job(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        source = Source(
            name="Example",
            feed_url="https://publication.example/rss",
            source_type="publisher",
            approval_status="pending",
            enabled=True,
        )
        session.add(source)
        session.flush()
        job = request_enrichment(session, source.id)
        identifiers = source.id, job.id
    parsed = ParsedFeed([SimpleNamespace(title=TITLE, summary="")] * 10, 10, 0)
    monkeypatch.setattr(source_relevance, "validate_feed", lambda *a, **kw: parsed)
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *a: ({}, None))
    return identifiers


@pytest.mark.integration
@pytest.mark.parametrize(
    "case", ["reject", "uncertain", "low_confidence", "mixed", "invalid", "sparse"]
)
def test_worker_applies_only_supported_rejection(database, monkeypatch, review_job, case):
    source_id, job_id = review_job
    result, _ = assessment(
        unrelated=7 if case == "mixed" else 8,
        confidence=0.8 if case == "low_confidence" else 0.95,
        verdict="uncertain" if case == "uncertain" else "unrelated",
    )
    if case == "invalid":
        result.entries[0].evidence = "Made-up evidence not present in the supplied feed"
    if case == "sparse":
        monkeypatch.setattr(
            source_relevance, "validate_feed", lambda *a, **kw: ParsedFeed([], 0, 0)
        )
    monkeypatch.setattr(
        source_relevance,
        "CodexClient",
        lambda *_: SimpleNamespace(complete=lambda *a: result.model_dump()),
    )
    source_tasks.enrich_source(str(job_id))
    source_tasks.enrich_source(str(job_id))  # Duplicate delivery must not duplicate review history.
    with database() as session:
        source = session.get(Source, source_id)
        job = session.get(SourceEnrichmentJob, job_id)
        assert source.approval_status == ("rejected" if case == "reject" else "pending")
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        reviews = session.scalars(select(SourceReview)).all()
        assert len(reviews) == int(case == "reject")
        if case == "reject":
            assert not source.enabled
            assert source.relevance_assessment["rejection_supported"] is True
            assert source.relevance_assessment["version"] == "source-relevance-v3"
            assert reviews[0].actor == "devfeed:source-relevance"
            assert (
                reviews[0].decision == "rejected" and "outside developer scope" in reviews[0].note
            )
            assert job.status == "succeeded"
        elif case == "invalid":
            assert job.status == "queued" and "source_evidence_not_in_sample" in job.error
            assert source.relevance_assessment == {}
        else:
            assert job.status == "succeeded"
            assert source.relevance_assessment["rejection_supported"] is False


@pytest.mark.integration
@pytest.mark.parametrize("during", ["approve", "reject", "disable", "feed_changed", "lease_lost"])
def test_auto_rejection_respects_concurrent_changes(database, monkeypatch, review_job, during):
    source_id, job_id = review_job
    result, _ = assessment()

    def complete(*args):
        with database.begin() as session:
            if during in {"approve", "reject"}:
                services.review_source(
                    session,
                    source_id,
                    SourceDecision(
                        decision="approved" if during == "approve" else "rejected",
                        actor="operator",
                        note="Manual decision",
                    ),
                )
            elif during == "feed_changed":
                session.get(Source, source_id).feed_url = "https://publication.example/other"
            elif during == "lease_lost":
                session.get(SourceEnrichmentJob, job_id).lease_token = None
            else:
                monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "false")
                get_settings.cache_clear()
        return result.model_dump()

    monkeypatch.setattr(
        source_relevance, "CodexClient", lambda *_: SimpleNamespace(complete=complete)
    )
    source_tasks.enrich_source(str(job_id))
    with database() as session:
        source = session.get(Source, source_id)
        assert source.approval_status == {"approve": "approved", "reject": "rejected"}.get(
            during, "pending"
        )
        assert not session.scalar(
            select(SourceReview.id).where(SourceReview.actor == "devfeed:source-relevance")
        )
