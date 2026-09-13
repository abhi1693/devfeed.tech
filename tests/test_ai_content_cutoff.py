from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks, source_relevance
from devfeed_core.ai_content import eligible_article, eligible_content
from devfeed_core.config import Settings, get_settings
from devfeed_core.source_relevance import feed_sample
from pydantic import ValidationError
from test_analysis_tasks import runtime as runtime


def set_cutoff(monkeypatch, value="2026-09-01"):
    monkeypatch.setenv("DEVFEED_AI_CONTENT_NOT_BEFORE", value)
    get_settings.cache_clear()


def test_cutoff_defaults_to_september_and_can_be_changed_or_disabled(monkeypatch):
    monkeypatch.delenv("DEVFEED_AI_CONTENT_NOT_BEFORE", raising=False)
    assert Settings().ai_content_not_before == date(2026, 9, 1)
    set_cutoff(monkeypatch, "2026-08-01")
    assert get_settings().ai_content_not_before == date(2026, 8, 1)
    set_cutoff(monkeypatch, "")
    assert get_settings().ai_content_not_before is None
    assert eligible_content(None)
    set_cutoff(monkeypatch, "not-a-date")
    with pytest.raises(ValidationError):
        get_settings()


@pytest.mark.parametrize(
    "published,expected",
    [
        ("2026-08-31T23:59:59+00:00", False),
        ("2026-09-01T00:00:00+00:00", True),
        ("2026-09-01T00:30:00+01:00", False),
        ("2026-08-31T23:30:00-01:00", True),
        ("2026-09-01T00:00:00", True),
        (None, False),
    ],
)
def test_source_publication_boundary_is_inclusive_utc(monkeypatch, published, expected):
    set_cutoff(monkeypatch)
    assert eligible_content(datetime.fromisoformat(published) if published else None) is expected


@pytest.mark.parametrize("published", [datetime(2026, 8, 1, tzinfo=UTC), None])
def test_recently_queued_old_or_undated_content_spends_no_inference(
    runtime, monkeypatch, published
):
    article, job, _, _ = runtime
    set_cutoff(monkeypatch)
    article.published_at = published
    article.discovered_at = job.created_at = datetime(2026, 9, 13, tzinfo=UTC)
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: pytest.fail("Started inference"))
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "content_date_deferred"
    assert job.attempts == 0 and job.lease_token is None
    assert article.review_status == "pending" and article.publication_status == "unpublished"
    assert article.ai_summary is None
    set_cutoff(monkeypatch, "")
    assert eligible_article(article)


def test_queue_or_discovery_time_does_not_exclude_recent_publication(runtime, monkeypatch):
    article, job, _, _ = runtime
    set_cutoff(monkeypatch)
    article.published_at = datetime(2026, 9, 1, tzinfo=UTC)
    article.discovered_at = job.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    analysis_tasks._analyze(job.id)
    assert job.outcome == "applied" and job.attempts == 1


def test_source_samples_filter_before_limiting_and_reindex(monkeypatch):
    set_cutoff(monkeypatch)
    old = SimpleNamespace(title="Old", summary="old", published_at=datetime(2026, 8, 31))
    new = SimpleNamespace(title="New", summary="new", published_at=datetime(2026, 9, 1))
    unknown = SimpleNamespace(title="Undated", summary="unknown", published_at=None)
    sample = feed_sample(SimpleNamespace(entries=[old] * 10 + [unknown, new, new, new]))
    assert sample == [{"index": i, "title": "New", "summary": "new"} for i in range(3)]


def test_source_with_only_old_content_does_not_call_ai(monkeypatch):
    set_cutoff(monkeypatch)
    parsed = SimpleNamespace(
        entries=[SimpleNamespace(title="Old", summary="old", published_at=datetime(2026, 8, 1))]
        * 5,
    )
    monkeypatch.setattr(source_relevance, "validate_feed", lambda *a, **kw: parsed)
    monkeypatch.setattr(source_relevance, "CodexClient", lambda _: pytest.fail("Started inference"))
    result = source_relevance.assess_source("https://example.com/rss", "publisher")
    assert result["relevance"] == "uncertain"
