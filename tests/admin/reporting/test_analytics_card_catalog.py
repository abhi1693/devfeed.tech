"""Native importer/reconciler catalog reuse, revision fencing and export budgets."""

import asyncio
import gc
import json
import os
import platform
import time
import weakref
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from devfeed_core import analysis
from devfeed_core import analytics_direct_cards as cards
from devfeed_core.analytics_import import HistoricalAnalyticsImport
from devfeed_core.analytics_reconcile import CurrentStateReconciler
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleAnalysisJob, Topic
from sqlalchemy import event, select, update

from tests.support.analytics_card_catalog import NOW, identifier, seed

pytestmark = pytest.mark.integration


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


@contextmanager
def costs(engine):
    counts = Counter(catalog_loads=0, ranking_preparations=0, sql_statements=0)

    def sql(connection, cursor, statement, parameters, context, executemany):
        counts["sql_statements"] += 1

    def catalog(session):
        counts["catalog_loads"] += 1
        return read_catalog(session)

    def prepare(self, taxonomy):
        counts["ranking_preparations"] += 1
        prepare_catalog(self, taxonomy)

    read_catalog, prepare_catalog = analysis._read_catalog, analysis._PreparedCatalog.__init__
    gc.collect()
    cpu, wall = time.process_time(), time.perf_counter()
    event.listen(engine, "before_cursor_execute", sql)
    try:
        with (
            patch.object(analysis, "_read_catalog", catalog),
            patch.object(analysis._PreparedCatalog, "__init__", prepare),
        ):
            yield counts
    finally:
        event.remove(engine, "before_cursor_execute", sql)
        counts["cpu_seconds"] = round(time.process_time() - cpu, 6)
        counts["wall_seconds"] = round(time.perf_counter() - wall, 6)


def test_native_card_catalog_is_reused_per_owner_and_rebuilt_after_committed_edits(
    database, monkeypatch
):
    # CI uses a compact fixture; the documented invocation runs the audited
    # production population with exactly the same native code and assertions.
    tags = int(os.environ.get("DEVFEED_CARD_CATALOG_TAGS", "256"))
    topics = int(os.environ.get("DEVFEED_CARD_CATALOG_TOPICS", "64"))
    article_count = int(os.environ.get("DEVFEED_CARD_CATALOG_ARTICLES", "12"))
    page_size = int(os.environ.get("DEVFEED_CARD_CATALOG_PAGE_SIZE", "4"))
    assert 1 <= tags <= 100_000 and 1 <= topics <= 10_000
    assert 1 <= page_size <= 200 and 3 * page_size <= article_count <= 1000
    articles, sources = seed(database, tags=tags, topics=topics, articles=article_count)
    settings = get_settings()
    engine = database.kw["bind"]
    monkeypatch.setattr(cards, "datetime", FrozenClock)
    sink = SimpleNamespace(insert=AsyncMock())
    reconciler = CurrentStateReconciler(sink, settings)
    importer = HistoricalAnalyticsImport(sink, cutover=NOW, settings=settings)
    pages = [articles[start : start + page_size] for start in range(0, len(articles), page_size)]
    source_pages = [sources[start : start + 4] for start in range(0, len(sources), 4)]
    report = {
        "tags": tags,
        "topics": topics,
        "article_pages": len(pages),
        "article_count": len(articles),
        "page_size": page_size,
        "architecture": platform.machine(),
    }

    async def observe():
        with costs(engine) as cold:
            baseline = [
                row
                for page in pages
                for row in await asyncio.to_thread(cards.build_cards, settings, "article", page, 1)
            ]
        report["cold_pages"] = dict(cold)
        assert cold["catalog_loads"] == cold["ranking_preparations"] == len(pages)
        for name, owner, build in (
            ("current_reconciler", reconciler, lambda page: reconciler._build("article", page, 1)),
            (
                "historical_import",
                importer,
                lambda page: importer.import_cards("article", [{"id": value} for value in page]),
            ),
        ):
            sink.insert.reset_mock()
            with costs(engine) as retained:
                result = []
                for page in pages:
                    rows = await build(page)
                    result.extend(
                        rows if name == "current_reconciler" else sink.insert.await_args.args[1]
                    )
            assert sorted(result, key=lambda row: row["entity_id"]) == sorted(
                baseline, key=lambda row: row["entity_id"]
            )
            assert retained["catalog_loads"] == retained["ranking_preparations"] == 1
            # Cheap revision reads replace catalog-row transfers; query count
            # alone is not a cost comparison for a multi-megabyte catalog.
            assert retained["sql_statements"] <= cold["sql_statements"] + len(pages)
            report[name] = dict(retained)
            previous = weakref.ref(owner._card_ranking[0])

            # A rolled-back database revision must not evict the committed generation.
            with database() as session:
                session.execute(
                    update(Topic)
                    .where(Topic.id == identifier("topic", 0))
                    .values(description="Rolled back routing definition")
                )
                session.rollback()
            with costs(engine) as rollback:
                await build(pages[0])
            assert rollback["catalog_loads"] == rollback["ranking_preparations"] == 0
            assert previous() is owner._card_ranking[0]

            with database.begin() as session:
                session.execute(
                    update(Topic)
                    .where(Topic.id == identifier("topic", 0))
                    .values(description=f"Committed new routing definition for {name}")
                )
            with costs(engine) as revised:
                changed = await build(pages[0])
                changed = (
                    changed if name == "current_reconciler" else sink.insert.await_args.args[1]
                )
                await build(pages[1])
            assert revised["catalog_loads"] == revised["ranking_preparations"] == 1
            expected = await asyncio.to_thread(cards.build_cards, settings, "article", pages[0], 1)
            assert sorted(changed, key=lambda row: row["entity_id"]) == sorted(
                expected, key=lambda row: row["entity_id"]
            )
            assert {row["semantic_hash"] for row in changed} != {
                row["semantic_hash"] for row in baseline if row["entity_id"] in pages[0]
            }
            assert all(
                "current_catalog_required" in json.loads(row["payload"])["blockers"]
                for row in changed
            )
            gc.collect()
            assert previous() is None

            # Restore fixture truth before measuring the independent next owner.
            with database.begin() as session:
                session.execute(
                    update(Topic)
                    .where(Topic.id == identifier("topic", 0))
                    .values(description="Development tools and application routing platform 0.")
                )

        # Source cards require no taxonomy loads or ranking, even with a large catalog.
        with costs(engine) as source_cost:
            source_rows = []
            for page in source_pages:
                source_rows.extend(await reconciler._build("source", page, 1))
                await importer.import_cards("source", [{"id": value} for value in page])
                assert sorted(
                    sink.insert.await_args.args[1], key=lambda row: row["entity_id"]
                ) == sorted(source_rows[-len(page) :], key=lambda row: row["entity_id"])
        assert source_cost["catalog_loads"] == source_cost["ranking_preparations"] == 0
        assert "private-fixture-value" not in json.dumps(source_rows, default=str)
        report["source_cards_both_owners"] = dict(source_cost)

    asyncio.run(observe())
    # Rendering remains observational: no jobs, classifications or public statuses change.
    with database() as session:
        assert set(session.scalars(select(ArticleAnalysisJob.status))) == {"succeeded"}
        assert set(session.scalars(select(Article.publication_status))) == {"unpublished"}
    print("CARD_CATALOG_BENCHMARK=" + json.dumps(report, sort_keys=True))
