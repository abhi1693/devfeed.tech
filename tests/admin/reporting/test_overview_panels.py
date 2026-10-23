"""Chart reads must not build the aggregate dashboard or block unrelated panels."""

import threading
from typing import get_args

import pytest
from devfeed_admin_api import overview
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.overview_panel_data import GROUPS, PanelName, panel_data
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    Source,
    utcnow,
)

pytestmark = pytest.mark.integration


def test_every_panel_loads_only_a_published_group(database, admin_client, monkeypatch):
    from devfeed_admin_api.overview_projection import report_key

    from tests.admin.reporting.projections.test_overview_projections import save_panel

    monkeypatch.setattr(
        overview, "overview_metrics", lambda *_: pytest.fail("aggregate loader called")
    )
    seen = set()
    for panel in get_args(PanelName):
        key = report_key(GROUPS.get(panel, panel), 7)
        if key not in seen:
            save_panel(database, *key)
            seen.add(key)
        response = admin_client.get(f"/v1/admin/overview/panels/{panel}?days=7")
        assert response.status_code == 200, (panel, response.text)
        assert response.json()["days"] == 7
        assert response.headers["cache-control"] == "no-store"
    assert admin_client.get("/v1/admin/overview/panels/unknown").status_code == 422
    assert admin_client.get("/v1/admin/overview/panels/readers?days=0").status_code == 422


def test_slow_publisher_does_not_block_another_chart_or_health(database, admin_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from devfeed_admin_api import overview_processing
    from devfeed_admin_api import overview_projection as projections
    from devfeed_admin_api.overview_panel_data import OverviewPanel

    from tests.admin.reporting.projections.test_overview_projections import save_panel

    entered, release = threading.Event(), threading.Event()
    save_panel(database, "sources", 30)
    with database.begin() as session:
        projections.request_refresh(session, "blockers", 1, utcnow())

    def slow(factory, group, days, now):
        entered.set()
        assert release.wait(8)
        return OverviewPanel(generated_at=now, days=days)

    monkeypatch.setattr(projections, "calculate_report", slow)
    monkeypatch.setattr(overview_processing, "refresh_processing_facts", lambda *_: False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(
            projections.refresh_overview_projections, database, limit=1, seed=False
        )
        try:
            assert entered.wait(3)
            assert admin_client.get("/v1/admin/overview/panels/blockers").status_code == 503
            assert admin_client.get("/v1/admin/overview/panels/sources").status_code == 200
            assert admin_client.get("/health/live").status_code == 200
        finally:
            release.set()
            assert worker.result(timeout=5) == 1
    assert admin_client.get("/v1/admin/overview/panels/blockers").status_code == 200


def test_charts_share_daily_documents_and_keep_ranges_independent(database, admin_client):
    from devfeed_core.models import ReportRefresh

    from tests.admin.reporting.projections.test_overview_projections import save_panel

    save_panel(database, "daily", 7)
    for chart in ("publications", "readers", "click-depth"):
        assert admin_client.get(f"/v1/admin/overview/panels/{chart}?days=7").status_code == 200
    assert admin_client.get("/v1/admin/overview/panels/readers?days=30").status_code == 503
    with database() as session:
        assert session.get(ReportRefresh, ("daily", 30)) is not None
        assert session.get(ReportRefresh, ("daily", 7)) is None


def test_panel_authentication_precedes_saved_response(database, admin_client, monkeypatch):
    from devfeed_admin_api import auth

    from tests.admin.reporting.projections.test_overview_projections import save_panel

    save_panel(database, "daily", 30)
    monkeypatch.setattr(auth, "require_config", lambda: None)
    assert admin_client.get("/v1/admin/overview/panels/readers").status_code == 200
    override = admin_client.app.dependency_overrides.pop(require_admin)
    try:
        assert admin_client.get("/v1/admin/overview/panels/readers").status_code == 401
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


def test_panels_use_only_their_small_data_source(database, monkeypatch):
    from devfeed_admin_api import overview_panel_data, overview_projection

    monkeypatch.setattr(
        overview_projection,
        "projected_blockers",
        lambda *_: pytest.fail("unrelated blockers query"),
    )
    monkeypatch.setattr(
        overview_panel_data, "inference_charts", lambda *_: pytest.fail("unrelated inference query")
    )
    with database() as session:
        result = panel_data(session, GROUPS["readers"], 7, utcnow())
    assert len(result.insights.reader_activity) == 7
    assert result.automation is None
