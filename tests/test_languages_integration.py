"""Language metadata and cached reader behavior on explicitly supplied test services."""

import pytest
from devfeed_aggregator import language_backfill, tasks
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import Article, Source
from sqlalchemy import select, update

pytestmark = pytest.mark.integration

JAPANESE = (
    "この記事では開発者向けのニュースを収集し、記事の言語を自動的に判定する方法について詳しく説明します。"
    "データベースに正しい情報を保存することで、読みたい言語の記事を簡単に探せるようになります。"
    "実際の例を使いながら、新しい機能を公開する前に確認すべき点も紹介します。"
)


@pytest.fixture
def imported_article(database, monkeypatch, rss_bytes):
    body = rss_bytes.replace(b"A practical <b>PostgreSQL</b> tutorial.", JAPANESE.encode())
    with database.begin() as session:
        source = Source(
            name="Multilingual publisher",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
            language="en",
        )
        session.add(source)
        session.flush()
        source_id, job_id = source.id, request_ingestion(session, source).id
    monkeypatch.setattr(tasks, "fetch_feed", lambda *args: FetchResult(200, body, args[0]))
    tasks.ingest(str(job_id))
    with database() as session:
        identifier = session.scalar(select(Article.id).where(Article.title.like("%Python%")))
    return source_id, identifier


def test_worker_infers_language_and_keeps_declared_origin_evidence(
    client, imported_article, publish_for_read_test
):
    source_id, article_id = imported_article
    publish_for_read_test()
    article = client.get(f"/v1/articles/{article_id}").json()
    assert article["language"] == "ja"
    assert article["origins"][0]["source_metadata"]["language"] == "en"
    assert client.get(f"/v1/sources/{source_id}").json()["language"] == "en"
    assert [a["id"] for a in client.get("/v1/feed?language=ja").json()["items"]] == [
        str(article_id)
    ]
    assert str(article_id) not in {
        a["id"] for a in client.get("/v1/feed?language=en").json()["items"]
    }


def test_backfill_corrects_legacy_hints_and_invalidates_only_on_real_change(
    client, database, imported_article, monkeypatch, publish_for_read_test
):
    _, article_id = imported_article
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    try:
        with database.begin() as session:
            article = session.get(Article, article_id)
            article.language = "en"
        path = f"/v1/articles/{article_id}"
        assert client.get(path).status_code == 404
        assert language_backfill.backfill_languages(dry_run=True)["updated"] == 0
        assert language_backfill.backfill_languages()["updated"] == 1
        assert client.get(path).status_code == 404
        publish_for_read_test()
        result = client.get(path)
        assert result.headers["x-cache"] == "MISS" and result.json()["language"] == "ja"
        assert result.json()["summary"] == JAPANESE
        assert client.get(path).headers["x-cache"] == "HIT"
        assert language_backfill.backfill_languages()["updated"] == 0
        assert client.get(path).headers["x-cache"] == "HIT"
    finally:
        cache.close_cache()


def test_backfill_does_not_overwrite_text_changed_during_inference(
    database, imported_article, monkeypatch
):
    _, article_id = imported_article
    with database.begin() as session:
        session.get(Article, article_id).language = "en"
    original = language_backfill.detect_language

    def concurrent_change(title, summary, source_type):
        if summary == JAPANESE:
            with database.begin() as session:
                session.execute(
                    update(Article).where(Article.id == article_id).values(summary="New text")
                )
        return original(title, summary, source_type)

    monkeypatch.setattr(language_backfill, "detect_language", concurrent_change)
    result = language_backfill.backfill_languages()
    assert result["concurrent_changes_skipped"] == 1 and result["updated"] == 0
    with database() as session:
        article = session.get(Article, article_id)
        assert article.summary == "New text" and article.language == "en"
