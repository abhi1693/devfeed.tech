"""Chart reads must not build the aggregate dashboard or block unrelated panels."""

import asyncio
import threading
from typing import get_args

import httpx
import pytest
from devfeed_admin_api import overview, overview_panels
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.overview_panel_data import GROUPS, PanelName, panel_data
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleEnrichmentJob, utcnow

pytestmark = pytest.mark.integration


def test_every_panel_loads_without_building_the_whole_overview(database, admin_client, monkeypatch):
    monkeypatch.setattr(
        overview, "overview_metrics", lambda *_: pytest.fail("aggregate loader called")
    )
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    for panel in get_args(PanelName):
        response = admin_client.get(f"/v1/admin/overview/panels/{panel}?days=7")
        assert response.status_code == 200, (panel, response.text)
        assert response.json()["days"] == 7
        assert response.headers["cache-control"] == "no-store"
    assert admin_client.get("/v1/admin/overview/panels/unknown").status_code == 422
    assert admin_client.get("/v1/admin/overview/panels/readers?days=0").status_code == 422


def test_slow_chart_does_not_block_another_chart_or_health(database, admin_client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = overview_panels.load_panel

    def slow(sessions, group, days):
        if group == "blockers":
            entered.set()
            assert release.wait(8)
        return original(sessions, group, days)

    monkeypatch.setattr(overview_panels, "load_panel", slow)
    monkeypatch.setattr(overview_panels, "WAIT_SECONDS", 0.5)

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_client.app), base_url="http://testserver"
        ) as client:
            blocked = asyncio.create_task(client.get("/v1/admin/overview/panels/blockers"))
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                quick, health = await asyncio.wait_for(
                    asyncio.gather(
                        client.get("/v1/admin/overview/panels/sources"), client.get("/health/live")
                    ),
                    2,
                )
                assert quick.status_code == health.status_code == 200
                response = await blocked
                assert response.status_code == 503
                assert response.headers["retry-after"] == "2"
            finally:
                release.set()
                await asyncio.gather(*list(admin_client.app.state.overview_panel_tasks.values()))
            assert (await client.get("/v1/admin/overview/panels/blockers")).status_code == 200

    asyncio.run(requests())


def test_charts_sharing_daily_data_coalesce_but_have_independent_requests(
    database, admin_client, monkeypatch
):
    loads = []
    original = overview_panels.load_panel

    def record(sessions, group, days):
        loads.append((group, days))
        return original(sessions, group, days)

    monkeypatch.setattr(overview_panels, "load_panel", record)
    for chart in ("publications", "readers", "click-depth"):
        assert admin_client.get(f"/v1/admin/overview/panels/{chart}?days=7").status_code == 200
    assert loads == [("daily", 7)]
    assert admin_client.get("/v1/admin/overview/panels/readers?days=30").status_code == 200
    assert loads == [("daily", 7), ("daily", 30)]


def test_panel_authentication_precedes_cached_response(database, admin_client, monkeypatch):
    from devfeed_admin_api import auth

    monkeypatch.setattr(auth, "require_config", lambda: None)
    assert admin_client.get("/v1/admin/overview/panels/readers").status_code == 200
    override = admin_client.app.dependency_overrides.pop(require_admin)
    try:
        assert admin_client.get("/v1/admin/overview/panels/readers").status_code == 401
    finally:
        admin_client.app.dependency_overrides[require_admin] = override


def test_pending_extraction_is_distinguished_from_unavailable_text(database):
    with database.begin() as session:
        queued = Article(
            canonical_url="https://example.com/queued",
            url_hash="a" * 64,
            title="Waiting",
            summary="",
        )
        empty = Article(
            canonical_url="https://example.com/empty", url_hash="b" * 64, title="Empty", summary=""
        )
        session.add_all([queued, empty])
        session.flush()
        session.add(ArticleEnrichmentJob(article_id=queued.id))
    with database() as session:
        result = panel_data(session, "blockers", 7, utcnow())
    counts = {x.code: x.count for x in result.automation.blockers}
    assert counts["awaiting_enrichment"] == counts["insufficient_text"] == 1


def test_panels_use_only_their_small_data_source(database, monkeypatch):
    from devfeed_admin_api import overview_panel_data

    monkeypatch.setattr(
        overview_panel_data,
        "automation_blockers",
        lambda *_: pytest.fail("unrelated blockers query"),
    )
    monkeypatch.setattr(
        overview_panel_data, "inference_charts", lambda *_: pytest.fail("unrelated inference query")
    )
    with database() as session:
        result = panel_data(session, GROUPS["readers"], 7, utcnow())
    assert len(result.insights.reader_activity) == 7
    assert result.automation is None
