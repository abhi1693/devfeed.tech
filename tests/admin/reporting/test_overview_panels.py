"""Chart reads must not build the aggregate dashboard or block unrelated panels."""

import threading
from typing import get_args

import pytest
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.overview_charts import CHART_SECTIONS, ChartName
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    Source,
    utcnow,
)

from tests.support.overview import window_for

pytestmark = pytest.mark.integration


def test_every_panel_loads_only_a_published_group(database, admin_client, monkeypatch):
    from devfeed_admin_api.overview_projection import report_key

    from tests.support.overview import save_section

    monkeypatch.setattr(
        projections, "calculate_report_async", lambda *_: pytest.fail("aggregate loader called")
    )
    seen = set()
    for panel in get_args(ChartName):
        section = CHART_SECTIONS[panel]
        key = report_key(section, window_for(section, 7, utcnow()))
        if key not in seen:
            save_section(database, section, 7)
            seen.add(key)
        response = admin_client.get(f"/v1/admin/overview/charts/{panel}?from=now-6d/d&to=now")
        assert response.status_code == 200, (panel, response.text)
        assert response.json()["days"] == (1 if panel == "attention" else 7)
        assert response.headers["cache-control"] == "no-store"
    assert admin_client.get("/v1/admin/overview/charts/unknown").status_code == 422
    assert admin_client.get("/v1/admin/overview/publishing").status_code == 404
    assert (
        admin_client.get(
            "/v1/admin/overview/charts/publication-rate?from=now+1d&to=now"
        ).status_code
        == 422
    )


def test_slow_publisher_does_not_block_another_chart_or_health(database, admin_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from devfeed_admin_api import overview_projection as projections

    from tests.support.overview import save_section, section_document

    entered, release = threading.Event(), threading.Event()
    save_section(database, "coverage", 30)
    with database.begin() as session:
        projections.request_refresh(
            session, "operations", window_for("operations", 1, utcnow()), utcnow()
        )

    def slow(factory, group, window, now):
        entered.set()
        assert release.wait(8)
        return section_document(group=group, generated_at=now, window=window)

    monkeypatch.setattr(projections, "calculate_report", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(projections.refresh_overview_projections, database, limit=1)
        try:
            assert entered.wait(3)
            assert admin_client.get("/v1/admin/overview/charts/attention").status_code == 503
            assert admin_client.get("/v1/admin/overview/charts/source-output").status_code == 200
            assert admin_client.get("/health/live").status_code == 200
        finally:
            release.set()
            assert worker.result(timeout=5) == 1
    assert admin_client.get("/v1/admin/overview/charts/attention").status_code == 200


def test_section_date_ranges_keep_independent_saved_documents(database, admin_client):
    from devfeed_core.models import ReportRefresh

    from tests.support.overview import save_section

    save_section(database, "publishing", 7)
    save_section(database, "audience", 7)
    for chart in ("publication-rate", "audience-activity"):
        assert (
            admin_client.get(f"/v1/admin/overview/charts/{chart}?from=now-6d/d&to=now").status_code
            == 200
        )
    assert (
        admin_client.get(
            "/v1/admin/overview/charts/audience-activity?from=now-29d/d&to=now"
        ).status_code
        == 503
    )
    with database() as session:
        assert (
            session.get(ReportRefresh, ("audience", window_for("audience", 30, utcnow()).key))
            is not None
        )
        assert (
            session.get(ReportRefresh, ("audience", window_for("audience", 7, utcnow()).key))
            is None
        )


def test_panel_authentication_precedes_saved_response(database, admin_client, monkeypatch):
    from devfeed_admin_api import auth

    from tests.support.overview import save_section

    save_section(database, "publishing", 30)
    monkeypatch.setattr(auth, "require_config", lambda: None)
    assert admin_client.get("/v1/admin/overview/charts/publication-rate").status_code == 200
    override = admin_client.app.dependency_overrides.pop(require_admin)
    try:
        assert admin_client.get("/v1/admin/overview/charts/publication-rate").status_code == 401
    finally:
        admin_client.app.dependency_overrides[require_admin] = override


def test_pending_extraction_is_distinguished_from_unavailable_text(database):
    with database.begin() as session:
        source = Source(
            name="Extraction test publisher",
            source_type="publisher",
            feed_url="https://example.com/feed",
            approval_status="approved",
            enabled=True,
        )
        queued = Article(
            canonical_url="https://example.com/queued",
            url_hash="a" * 64,
            title="Waiting",
            summary="",
        )
        empty = Article(
            canonical_url="https://example.com/empty", url_hash="b" * 64, title="Empty", summary=""
        )
        cyrillic = Article(
            canonical_url="https://example.com/cyrillic",
            url_hash="c" * 64,
            title="Текст статьи",
            summary="",
        )
        summary_fallback = Article(
            canonical_url="https://example.com/summary-fallback",
            url_hash="e" * 64,
            title="Summary fallback",
            summary="A usable feed summary.",
        )
        failed = Article(
            canonical_url="https://example.com/failed",
            url_hash="d" * 64,
            title="Failed",
            summary="",
        )
        articles = [queued, empty, cyrillic, summary_fallback, failed]
        session.add_all([source, *articles])
        session.flush()
        session.add_all(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key=article.url_hash,
                original_url=article.canonical_url,
            )
            for article in articles
        )
        session.add(ArticleEnrichmentJob(article_id=queued.id))
        session.add(
            ArticleContent(
                article_id=cyrillic.id,
                url=cyrillic.canonical_url,
                text="Подробный текст статьи на русском языке.",
                content_hash="e" * 64,
                method="body",
            )
        )
        session.add(
            ArticleContent(
                article_id=summary_fallback.id,
                url=summary_fallback.canonical_url,
                text="   ",
                content_hash="f" * 64,
                method="body",
            )
        )
        session.add(
            ArticleEnrichmentJob(
                article_id=failed.id,
                status="failed",
                error="Article lookup failed: http_error",
            )
        )
    with database() as session:
        from devfeed_admin_api.automation import automation_blockers

        result = automation_blockers(session)
    counts = {x.code: x.count for x in result}
    assert counts["source_ineligible"] == 0
    assert counts["awaiting_enrichment"] == 1
    assert counts["insufficient_text"] == 2
    assert counts["enrichment_failed"] == 1


def test_equal_length_historical_windows_keep_their_exact_documents_and_totals(
    database, admin_client
):
    from datetime import date

    from devfeed_admin_api.overview_windows import ReportWindow

    from tests.support.overview import save_section

    first = ReportWindow(date(2025, 1, 1), date(2025, 1, 8))
    second = ReportWindow(date(2025, 2, 1), date(2025, 2, 8))
    save_section(database, "publishing", window=first, count=17)
    save_section(database, "publishing", window=second, count=91)
    for window, expected in [(first, 17), (second, 91)]:
        result = admin_client.get(
            f"/v1/admin/overview/charts/publication-rate?from={window.start}&to={window.inclusive_end}"
        )
        assert result.status_code == 200
        body = result.json()
        assert body["data"]["summary"]["published"] == expected
        assert (
            body["start"] == window.start.isoformat()
            and body["end"] == window.inclusive_end.isoformat()
        )
        assert body["days"] == 7


@pytest.mark.parametrize("chart", ["attention", "publication-rate"])
@pytest.mark.parametrize(
    "query", ["days=7", "start=2025-01-01", "end=2025-02-01", "range=P0D", "range=P367D"]
)
def test_removed_or_invalid_range_protocols_are_rejected_before_recording_demand(
    database, admin_client, chart, query
):
    from devfeed_core.models import ReportRefresh
    from sqlalchemy import func, select

    response = admin_client.get(f"/v1/admin/overview/charts/{chart}?{query}")
    assert response.status_code == 422
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 0
