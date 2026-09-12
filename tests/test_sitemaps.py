import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core import sitemaps
from devfeed_core.cache import CacheUnavailable, close_cache, get_cache
from devfeed_core.db import get_engine
from devfeed_core.models import Article, Source, Topic
from sqlalchemy import event, update
from test_public_search_integration import seed

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_cache(database):
    close_cache()
    yield
    close_cache()


def capture_sql():
    statements = []

    def capture(*args):
        statements.append(args[2])

    event.listen(get_engine(), "before_cursor_execute", capture)
    return statements, lambda: event.remove(get_engine(), "before_cursor_execute", capture)


def test_snapshot_caches_all_kinds_and_shards_without_sql_on_hits(client, database, monkeypatch):
    _, _, _, articles = seed(database)
    monkeypatch.setattr(sitemaps, "PART_SIZE", 1)
    queries, stop = capture_sql()
    try:
        response = client.get("/v1/sitemaps")
        assert response.status_code == 200
        manifest = response.json()
        assert [p["kind"] for p in manifest["parts"]].count("articles") == 2
        assert {p["kind"] for p in manifest["parts"]} == set(sitemaps.KINDS)
        assert queries
        queries.clear()
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: client.get("/v1/sitemaps"), range(16)))
        assert all(r.json() == manifest for r in responses)
        paths = []
        for part in manifest["parts"]:
            url = f"/v1/sitemaps/{part['kind']}/{part['page']}?v={manifest['generation']}"
            result = client.get(url)
            assert result.status_code == 200
            assert result.headers["cache-control"].startswith("public")
            paths.extend(result.json()["paths"])
        assert len([p for p in paths if p.startswith("/articles/")]) == 2
        assert "/tags/kubernetes" in paths and "/topics/kubernetes" in paths
        assert client.get("/v1/sitemaps/admin/1").status_code == 404
        assert client.get("/v1/sitemaps/articles/50001").status_code == 404
        assert (
            client.get("/v1/sitemaps/articles/999?v=" + manifest["generation"]).status_code == 404
        )
        assert not queries
    finally:
        stop()


def test_refresh_keeps_previous_version_readable_and_excludes_withdrawn_content(client, database):
    source, topic, tag, articles = seed(database)
    first = client.get("/v1/sitemaps").json()
    with database.begin() as session:
        session.execute(
            update(Source).where(Source.id == source).values(approval_status="rejected")
        )
    # Routine app writes do not make crawlers regenerate all sitemaps.
    assert client.get("/v1/sitemaps").json() == first
    previous_path = f"/v1/sitemaps/articles/1?v={first['generation']}"
    old = client.get(previous_path).json()
    cache = get_cache()
    key = cache.namespace + ":sitemaps:v1:manifest"
    expired = dict(first, built_at=0)
    cache.redis.set(key, json.dumps(expired), ex=3900)
    second = client.get("/v1/sitemaps").json()
    assert second["generation"] != first["generation"]
    assert second["parts"] == []
    assert client.get(previous_path).json() == old
    assert client.get("/v1/tags/kubernetes").status_code == 404


def test_inactive_topics_disabled_sources_and_unpublished_articles_are_excluded(client, database):
    source, topic, tag, articles = seed(database)
    assert client.get("/v1/tags/kubernetes").status_code == 200
    with database.begin() as session:
        session.execute(update(Topic).where(Topic.id == topic).values(status="rejected"))
        session.execute(update(Source).where(Source.id == source).values(enabled=False))
        session.execute(
            update(Article)
            .where(Article.id == articles[0])
            .values(publication_status="unpublished")
        )
    data = client.get("/v1/sitemaps").json()
    assert {p["kind"] for p in data["parts"]} == {"articles", "tags"}
    assert client.get(f"/v1/sitemaps/topics/1?v={data['generation']}").status_code == 404
    assert client.get(f"/v1/sitemaps/sources/1?v={data['generation']}").status_code == 404
    assert len(client.get(f"/v1/sitemaps/articles/1?v={data['generation']}").json()["paths"]) == 1


def test_cold_lock_and_cache_outage_never_fall_back_to_database(client, monkeypatch):
    cache = get_cache()
    cache.redis.set(cache.namespace + ":sitemaps:v1:lock", "other-worker", ex=60)
    monkeypatch.setattr(sitemaps, "session_factory", lambda: pytest.fail("Unexpected DB read"))
    assert client.get("/v1/sitemaps").status_code == 503

    def unavailable(*args):
        raise CacheUnavailable

    monkeypatch.setattr(sitemaps, "_redis", unavailable)
    response = client.get("/v1/sitemaps")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "5"


def test_failed_refresh_serves_previous_snapshot_without_publishing_partial_index(
    client, database, monkeypatch
):
    seed(database)
    previous = client.get("/v1/sitemaps").json()
    previous["built_at"] = 0
    get_cache().redis.set(
        get_cache().namespace + ":sitemaps:v1:manifest", json.dumps(previous), ex=3900
    )

    attempts = []

    def fail(*args):
        attempts.append(True)
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(sitemaps, "build_snapshot", fail)
    assert client.get("/v1/sitemaps").json() == previous
    assert client.get("/v1/sitemaps").json() == previous
    assert attempts == [True]
    assert not get_cache().redis.exists(get_cache().namespace + ":sitemaps:v1:lock")


def test_refresh_completed_before_lease_acquisition_is_reused(client, database, monkeypatch):
    seed(database)
    current = client.get("/v1/sitemaps").json()
    original = sitemaps._read
    reads = []

    def stale_first_read(key):
        reads.append(key)
        return dict(current, built_at=0) if len(reads) == 1 else original(key)

    monkeypatch.setattr(sitemaps, "_read", stale_first_read)
    monkeypatch.setattr(sitemaps, "session_factory", lambda: pytest.fail("Duplicate refresh"))
    assert client.get("/v1/sitemaps").json() == current


def test_empty_archive_does_not_publish_empty_sitemap_parts(client):
    response = client.get("/v1/sitemaps")
    assert response.status_code == 200
    assert response.json()["parts"] == []
    assert client.get("/v1/sitemaps/articles/1").status_code == 404


def test_old_manifest_format_is_refreshed_without_removing_old_part_urls(client, database):
    seed(database)
    previous = client.get("/v1/sitemaps").json()
    old_part = f"/v1/sitemaps/articles/1?v={previous['generation']}"
    old_data = client.get(old_part).json()
    del previous["format"]
    get_cache().redis.set(
        get_cache().namespace + ":sitemaps:v1:manifest", json.dumps(previous), ex=3900
    )
    current = client.get("/v1/sitemaps").json()
    assert current["format"] == sitemaps.SNAPSHOT_FORMAT
    assert current["generation"] != previous["generation"]
    assert client.get(old_part).json() == old_data


def test_stable_parts_preserve_lastmod_until_their_contents_change(client, database):
    _, _, _, articles = seed(database)
    first = client.get("/v1/sitemaps").json()
    key = get_cache().namespace + ":sitemaps:v1:manifest"
    get_cache().redis.set(key, json.dumps(dict(first, built_at=0)), ex=3900)
    second = client.get("/v1/sitemaps").json()
    assert second["generation"] != first["generation"]
    assert second["parts"] == first["parts"]
    with database.begin() as session:
        session.execute(update(Article).where(Article.id == articles[0]).values(slug="new-slug"))
    get_cache().redis.set(key, json.dumps(dict(second, built_at=0)), ex=3900)
    third = client.get("/v1/sitemaps").json()
    for old, new in zip(second["parts"], third["parts"], strict=True):
        if new["kind"] == "articles":
            assert new["digest"] != old["digest"]
            assert new["modified_at"] > old["modified_at"]
        else:
            assert new == old
    result = client.get("/v1/sitemaps/articles/1")
    assert result.status_code == 200
    assert "/articles/new-slug" in result.json()["paths"]


def test_lastmod_uses_publication_dates_across_all_collections(client, database):
    from datetime import UTC, datetime

    _, _, _, articles = seed(database)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    with database.begin() as session:
        session.execute(
            update(Article).where(Article.id == articles[0]).values(published_to_feed_at=older)
        )
        session.execute(
            update(Article).where(Article.id == articles[1]).values(published_to_feed_at=newer)
        )
        # Unpublished content must not influence public modification dates.
        session.execute(
            update(Article)
            .where(Article.id == articles[2])
            .values(published_to_feed_at=datetime(2027, 1, 1, tzinfo=UTC))
        )
    manifest = client.get("/v1/sitemaps").json()
    assert set(manifest["latest_publication"].values()) == {newer.isoformat()}
    for kind in sitemaps.KINDS:
        entries = client.get(f"/v1/sitemaps/{kind}/1").json()["entries"]
        dates = {entry["lastmod"] for entry in entries}
        assert dates == (
            {older.isoformat(), newer.isoformat()} if kind == "articles" else {newer.isoformat()}
        )
