from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from devfeed_aggregator import article_tasks, tasks
from devfeed_aggregator.tag_backfill import backfill_tags
from devfeed_core.analysis import Classifications, replace_classifications
from devfeed_core.article_jobs import request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.models import (
    Article,
    ArticleEnrichmentJob,
    ArticleOrigin,
    ArticleTag,
    Source,
    Tag,
    utcnow,
)
from devfeed_core.source_tags import attach_source_tags, resolve_source_tags
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("manual", [False, True])
def test_page_labels_fill_empty_feed_tags_and_preserve_editorial_classification(
    database, monkeypatch, admin_client, manual
):
    source_id = source(database)
    with database.begin() as session:
        tasks.store_entries(session, source_id, feed([]))
        article = session.scalar(select(Article))
        article.classification_provenance = {"origin": "manual" if manual else "ai"}
        article_id = article.id
        job_id = request_article_enrichment(session, article_id).id
    page = b"""<html><head><title>Article with source labels</title></head><body><article>
    <h1>Article with source labels</h1><aside><ul>
    <li><a href="/posts/category/kubernetes">Kubernetes</a></li>
    <li><a href="/posts/category/security">Security</a></li>
    </ul></aside></article></body></html>"""
    monkeypatch.setattr(
        article_tasks, "fetch_article_page", lambda url: FetchResult(200, page, url)
    )
    article_tasks.enrich_article(str(job_id))
    expected = set() if manual else {"Kubernetes", "Security"}
    with database() as session:
        article = session.get(Article, article_id)
        assert {tag.name for tag in article.tags} == expected
        assert not article.topic_links
        assert article.publication_status == "unpublished"
        assert session.get(ArticleEnrichmentJob, job_id).result["tags"] == [
            "Kubernetes",
            "Security",
        ]
        assert session.get(ArticleEnrichmentJob, job_id).outcome == (
            "not_found" if manual else "metadata_only"
        )
        assert set(session.scalars(select(ArticleTag.origin))) == (set() if manual else {"source"})
    response = admin_client.get(f"/v1/admin/articles/{article_id}").json()
    assert {tag["name"] for tag in response["tags"]} == expected
    with database.begin() as session:
        repeat_id = request_article_enrichment(session, article_id).id
    article_tasks.enrich_article(str(repeat_id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Tag)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleTag)) == len(expected)
        assert "tags" not in session.get(ArticleEnrichmentJob, repeat_id).changed_fields


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


@pytest.mark.parametrize("shared_cache", [False, True])
def test_repeated_fetch_restores_old_missing_tags_without_recreating_or_reclassifying_articles(
    database, monkeypatch, shared_cache
):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    from devfeed_core import source_tags

    monkeypatch.setattr(get_settings(), "source_tag_shared_cache_enabled", shared_cache)
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
    monkeypatch.setattr(source_tags, "_identity_snapshot", None)
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


def test_source_tag_projection_reuses_validated_data_after_a_fresh_worker(database, monkeypatch):
    import json
    import time
    import zlib

    from devfeed_core import cache, source_tags
    from devfeed_core.db import get_engine
    from sqlalchemy import event, text

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(get_settings(), "source_tag_shared_cache_enabled", True)
    cache.close_cache()
    with database.begin() as session:
        session.execute(
            text(
                "INSERT INTO tags(id,name,slug,aliases) "
                "SELECT md5('source-tag-shared-'||g::text)::uuid,'Tool '||g,'tool-'||g,"
                "ARRAY['alias-'||g] FROM generate_series(1,55000) g"
            )
        )
    queries = []
    normalized = []
    normalize = source_tags.source_tag_names

    def counted(values):
        normalized.append(1)
        return normalize(values)

    monkeypatch.setattr(source_tags, "source_tag_names", counted)

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "tags.aliases" in statement and "ORDER BY tags.slug" in statement:
            queries.append(statement)

    event.listen(get_engine(), "before_cursor_execute", observe)
    try:
        monkeypatch.setattr(source_tags, "_identity_snapshot", None)
        started = time.process_time()
        with database() as session:
            first, created = resolve_source_tags(session, ["alias-1", "Tool 2"])
        cold_cpu = time.process_time() - started
        cold_normalizations = len(normalized)
        assert cold_normalizations == 110001
        assert created == 0 and len(first) == 2 and len(queries) == 1
        # Simulate a new RQ work horse with no inherited in-process projection.
        monkeypatch.setattr(source_tags, "_identity_snapshot", None)
        started = time.process_time()
        with database() as session:
            repeated, created = resolve_source_tags(session, ["alias-1", "Tool 2"])
        warm_cpu = time.process_time() - started
        assert len(normalized) == cold_normalizations + 1
        payload = cache.get_cache().redis.getrange(
            f"{cache.get_cache().namespace}:source-tag-identities:v1",
            0,
            source_tags._SHARED_INDEX_BYTES,
        )
        print(
            json.dumps(
                {
                    "tags": 55000,
                    "cold_cpu_seconds": cold_cpu,
                    "warm_cpu_seconds": warm_cpu,
                    "wire_bytes": len(payload),
                    "decoded_bytes": len(zlib.decompress(payload)),
                    "cold_normalizations": cold_normalizations,
                    "warm_normalizations": 1,
                }
            )
        )
        assert repeated == first and created == 0 and len(queries) == 1
        with database.begin() as session:
            session.execute(text("UPDATE tags SET aliases=ARRAY['renamed'] WHERE slug='tool-1'"))
        monkeypatch.setattr(source_tags, "_identity_snapshot", None)
        with database() as session:
            changed, created = resolve_source_tags(session, ["renamed"])
        assert changed["renamed"] == first["alias-1"] and created == 0 and len(queries) == 2
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe)
        cache.close_cache()


@pytest.mark.parametrize(
    "failure",
    [
        "malformed",
        "bad_uuid",
        "bad_reference",
        "oversized",
        "decoded_oversized",
        "trailing",
        "unavailable",
    ],
)
def test_source_tag_shared_projection_failure_falls_back_to_postgres(
    database, monkeypatch, failure
):
    import json

    from devfeed_core import cache, source_tags
    from devfeed_core.models import CatalogRevision

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(get_settings(), "source_tag_shared_cache_enabled", True)
    cache.close_cache()
    with database.begin() as session:
        tag = Tag(name="Python", slug="python", aliases=["py"])
        session.add(tag)
        session.flush()
        identifier = tag.id
        revision = session.scalar(
            select(CatalogRevision.revision).where(CatalogRevision.name == "tags")
        )
    client = cache.get_cache()
    key = f"{client.namespace}:source-tag-identities:v1"
    if failure == "malformed":
        body = b"not-json"
    elif failure == "bad_uuid":
        import zlib

        body = zlib.compress(
            json.dumps(
                {
                    "revision": str(revision),
                    "ids": ["invalid"],
                    "identities": {"py": 0},
                    "slugs": {},
                }
            ).encode()
        )
    elif failure == "bad_reference":
        import zlib

        body = zlib.compress(
            json.dumps(
                {
                    "revision": str(revision),
                    "ids": [str(identifier)],
                    "identities": {"py": 2},
                    "slugs": {},
                }
            ).encode()
        )
    else:
        body = "x" * (source_tags._SHARED_INDEX_BYTES + 1)
    if failure in {"decoded_oversized", "trailing"}:
        import zlib

        body = zlib.compress(b"x" * (source_tags._SHARED_INDEX_DECODED_BYTES + 1))
        if failure == "trailing":
            body = zlib.compress(b"{}") + b"extra"
    client.redis.set(key, body)
    if failure == "unavailable":

        def unavailable(*_):
            raise cache.CacheUnavailable

        monkeypatch.setattr(client, "_run", unavailable)
    monkeypatch.setattr(source_tags, "_identity_snapshot", None)
    try:
        with database() as session:
            found, created = resolve_source_tags(session, ["py"])
        assert found == {"py": identifier} and created == 0
    finally:
        cache.close_cache()


def test_uncommitted_source_tag_projection_cannot_survive_catalog_rollback(database, monkeypatch):
    from devfeed_core import cache, source_tags

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(get_settings(), "source_tag_shared_cache_enabled", True)
    cache.close_cache()
    with database.begin() as session:
        tag = Tag(name="Stable", slug="stable", aliases=["old"])
        session.add(tag)
        session.flush()
        identifier = tag.id
    try:
        with database() as session:
            session.get(Tag, identifier).aliases = ["temporary"]
            monkeypatch.setattr(source_tags, "_identity_snapshot", None)
            assert resolve_source_tags(session, ["temporary"])[0] == {"temporary": identifier}
            session.rollback()
        monkeypatch.setattr(source_tags, "_identity_snapshot", None)
        with database() as session:
            assert resolve_source_tags(session, ["old"])[0] == {"old": identifier}
            assert session.get(Tag, identifier).aliases == ["old"]
    finally:
        cache.close_cache()
