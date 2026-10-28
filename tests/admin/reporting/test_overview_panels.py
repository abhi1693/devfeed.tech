"""Chart reads must not build the aggregate dashboard or block unrelated panels."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    Source,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def overview_cutover_database(integration_environment, monkeypatch):
    from tests.support.legacy_migrations import database_at_revision

    # Each scenario owns its schema, independently of whether another integration
    # test already completed the finite historical import in the shared test DB.
    with database_at_revision(monkeypatch, "head") as database:
        yield database


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

        result = automation_blockers(session, [article.id for article in articles])
    counts = {x.code: x.count for x in result}
    assert counts["source_ineligible"] == 0
    assert counts["awaiting_enrichment"] == 1
    assert counts["insufficient_text"] == 2
    assert counts["enrichment_failed"] == 1


@pytest.mark.parametrize("chart", ["attention", "publication-rate"])
@pytest.mark.parametrize(
    "cutover_complete", [False, True], ids=["import-pending", "import-complete"]
)
@pytest.mark.parametrize(
    "query", ["days=7", "start=2025-01-01", "end=2025-02-01", "range=P0D", "range=P367D"]
)
def test_removed_or_invalid_range_protocols_are_rejected_before_recording_demand(
    overview_cutover_database, admin_client, monkeypatch, chart, query, cutover_complete
):
    from devfeed_core.analytics_cutover import LegacyAnalyticsCutover
    from devfeed_core.analytics_import_runner import postgres_connections
    from devfeed_core.config import get_settings
    from devfeed_core.db import get_engine
    from sqlalchemy import event, text

    database = overview_cutover_database
    with database.begin() as session:
        session.execute(
            text("INSERT INTO report_refreshes(\"group\", days) VALUES ('publishing',7)")
        )

    async def prepare_cutover():
        async with postgres_connections(get_settings().database_url)() as connection:
            cutover = LegacyAnalyticsCutover()
            await cutover.freeze(connection)
            if cutover_complete:
                await cutover.cleanup(connection)

    asyncio.run(prepare_cutover())
    with database() as session:
        relation = session.scalar(text("SELECT to_regclass('report_refreshes')"))
        assert relation == (None if cutover_complete else "report_refreshes")
        previous_demand = (
            session.execute(text("SELECT * FROM report_refreshes")).all() if relation else []
        )

    section = AsyncMock(side_effect=AssertionError("Invalid chart ranges must not query analytics"))
    monkeypatch.setattr(admin_client.app.state.analytics, "section", section)
    statements = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        response = admin_client.get(f"/v1/admin/overview/charts/{chart}?{query}")
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert response.status_code == 422
    section.assert_not_called()
    assert statements == []
    with database() as session:
        assert session.scalar(text("SELECT to_regclass('report_refreshes')")) == relation
        if relation:
            assert session.execute(text("SELECT * FROM report_refreshes")).all() == previous_demand
