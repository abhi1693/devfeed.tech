from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from devfeed_aggregator import tasks
from devfeed_aggregator.tag_backfill import backfill_tags
from devfeed_core.analysis import Classifications, replace_classifications
from devfeed_core.config import get_settings
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, Source, Tag, utcnow
from devfeed_core.source_tags import attach_source_tags, resolve_source_tags
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration


def feed(labels, *, path="one", title="A new article"):
    categories = "".join(f"<category>{label}</category>" for label in labels)
    body = (
        '<rss version="2.0"><channel><title>Example</title><item>'
        f"<guid>{path}</guid><title>{title}</title><link>https://example.com/{path}</link>"
        "<description>A useful article with enough summary for ingestion.</description>"
        f"{categories}</item></channel></rss>"
    )
    return parse_feed(body.encode(), "https://example.com/feed", utcnow(), source_type="publisher")


def source(database, suffix="one"):
    with database.begin() as session:
        row = Source(
            name="Example",
            feed_url=f"https://example.com/{suffix}/rss",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(row)
        session.flush()
        return row.id


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_ingestion_imports_explicit_tags_and_reuses_names_and_aliases(
    database, admin_client, monkeypatch, ai_enabled
):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", str(ai_enabled).lower())
    get_settings.cache_clear()
    source_id = source(database)
    with database.begin() as session:
        session.add(Tag(name="Kubernetes", slug="cluster-platform", aliases=["k8s"]))
    parsed = feed(
        ["k8s", " New Label ", "new label", "C++", "C#", "!!!"], title="Python FastAPI tutorial"
    )
    with database.begin() as session:
        assert tasks.store_entries(session, source_id, parsed) == 1
    with database() as session:
        article = session.scalar(select(Article))
        article_id = article.id
        assert {tag.slug for tag in article.tags} == {
            "cluster-platform",
            "new-label",
            "c-plus-plus",
            "c-sharp",
        }
        assert not article.topic_links and article.publication_status == "unpublished"
        assert set(session.scalars(select(ArticleTag.origin))) == {"source"}
    assert admin_client.get("/v1/admin/tags").json()["total"] == 4
    assert len(admin_client.get(f"/v1/admin/articles/{article_id}").json()["tags"]) == 4
    with database.begin() as session:
        assert tasks.store_entries(session, source_id, parsed) == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Tag)) == 4
        assert session.scalar(select(func.count()).select_from(ArticleTag)) == 4


def test_repeated_fetch_restores_old_missing_tags_without_recreating_or_reclassifying_articles(
    database, monkeypatch
):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    source_id = source(database)
    parsed = feed(["supplied"])
    with database.begin() as session:
        tasks.store_entries(session, source_id, parsed)
        article = session.scalar(select(Article))
        article.classification_provenance = {"origin": "ai"}
        article_id, original_date = article.id, article.feed_at
        session.execute(delete(ArticleTag))
    with database.begin() as session:
        assert tasks.store_entries(session, source_id, parsed) == 0
        article = session.get(Article, article_id)
        assert [tag.slug for tag in article.tags] == ["supplied"]
        assert article.classification_provenance == {"origin": "ai"}
        assert article.feed_at == original_date
        article.classification_provenance = {"origin": "manual"}
        session.execute(delete(ArticleTag))
    with database.begin() as session:
        tasks.store_entries(session, source_id, parsed)
        assert not session.get(Article, article_id).tags
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 1


def test_source_and_manual_links_survive_ai_reanalysis_but_manual_edits_can_remove_them(database):
    source_id = source(database)
    with database.begin() as session:
        tasks.store_entries(session, source_id, feed(["supplied"]))
        article = session.scalar(select(Article))
        resolved, _ = resolve_source_tags(session, ["manual", "heuristic"])
        session.add_all(
            [
                ArticleTag(article_id=article.id, tag_id=resolved["manual"], origin="manual"),
                ArticleTag(article_id=article.id, tag_id=resolved["heuristic"], origin="heuristic"),
            ]
        )
        session.flush()
        result = Classifications(
            developer_relevance="relevant",
            language="en",
            content_type="article",
            content_format="article",
            topics=[],
            tags=[],
        )
        replace_classifications(session, article, result, origin="ai")
        assert set(session.scalars(select(ArticleTag.origin))) == {"manual", "source"}
        replace_classifications(session, article, result, origin="manual")
        assert session.scalars(select(ArticleTag)).all() == []


def test_supplied_evidence_promotes_automated_links_without_overwriting_manual_links(database):
    source_id = source(database)
    with database.begin() as session:
        tasks.store_entries(session, source_id, feed([]))
        article = session.scalar(select(Article))
        ids, _ = resolve_source_tags(session, ["ai", "manual", "heuristic"])
        session.add_all(
            [
                ArticleTag(article_id=article.id, tag_id=identifier, origin=name)
                for name, identifier in ids.items()
            ]
        )
        session.flush()
        assert attach_source_tags(session, article.id, ids.values()) == 2
        assert session.get(ArticleTag, (article.id, ids["manual"])).origin == "manual"
        assert session.get(ArticleTag, (article.id, ids["ai"])).origin == "source"


def test_concurrent_feeds_create_shared_tags_once_without_deadlock(database):
    source_ids = [source(database, str(i)) for i in range(2)]
    barrier = Barrier(2)

    def ingest(index):
        with database.begin() as session:
            barrier.wait(timeout=10)
            return tasks.store_entries(
                session,
                source_ids[index],
                feed(["beta", "alpha"][:: 1 if index else -1], path=str(index)),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(ingest, range(2))) == [1, 1]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Tag)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleTag)) == 4


def test_backfill_restores_retained_tags_in_bounded_idempotent_batches(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    source_id = source(database)
    with database.begin() as session:
        for path in ["one", "two"]:
            tasks.store_entries(session, source_id, feed(["saved", path], path=path))
        session.execute(delete(Tag))  # Legacy installation retained evidence but had no tags.
    preview = backfill_tags(limit=500, dry_run=True)
    assert (preview["tags_created"], preview["links_saved"]) == (3, 4)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Tag)) == 0
    first = backfill_tags(limit=1)
    assert first["origins_examined"] == 1 and first["next_after"]
    from uuid import UUID

    second = backfill_tags(limit=500, after=UUID(first["next_after"]))
    assert second["origins_examined"] == 1 and second["next_after"] is None
    repeated = backfill_tags(limit=500)
    assert repeated["tags_created"] == repeated["links_saved"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleTag)) == 4


def test_backfill_respects_source_review_and_manual_classifications(database):
    source_ids = [source(database, str(i)) for i in range(2)]
    with database.begin() as session:
        for index, source_id in enumerate(source_ids):
            tasks.store_entries(session, source_id, feed([f"label-{index}"], path=str(index)))
        session.execute(delete(ArticleTag))
        session.get(Source, source_ids[0]).approval_status = "rejected"
        for article in session.scalars(select(Article)):
            article.classification_provenance = {"origin": "manual"}
    result = backfill_tags(limit=500)
    assert result["origins_examined"] == 1 and result["links_saved"] == 0
