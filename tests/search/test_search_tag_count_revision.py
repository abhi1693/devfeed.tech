"""Unchanged reconciliations reuse counts without hiding committed visibility changes."""

import pytest
from devfeed_core import search_index
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, Source, Tag
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_tag_count_reuses_snapshot_and_revocations_rollback_and_deletion_are_fresh(
    database, monkeypatch
):
    monkeypatch.setenv("DEVFEED_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.test/rss",
            source_type="publisher",
            approval_status="approved",
        )
        article = Article(
            title="Visible",
            canonical_url="https://example.test/article",
            url_hash="a" * 64,
            review_status="approved",
            publication_status="published",
        )
        tag = Tag(name="Visible tag", slug="visible-tag")
        session.add_all([source, article, tag])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="one",
                original_url=article.canonical_url,
            )
        )
        session.add(ArticleTag(article_id=article.id, tag_id=tag.id))
        article_id, source_id = article.id, source.id
    query = search_index._visible_tag_count_query
    calls = []

    def counted():
        calls.append(True)
        return query()

    monkeypatch.setattr(search_index, "_visible_tag_count_query", counted)
    for _ in range(5):
        with database() as session:
            assert search_index._visible_tag_count(session) == 1
    assert len(calls) == 1
    for status, expected in (("unpublished", 0), ("published", 1)):
        with database.begin() as editor:
            editor.execute(
                text("UPDATE articles SET publication_status=:status WHERE id=:id"),
                {"status": status, "id": article_id},
            )
        with database() as session:
            assert search_index._visible_tag_count(session) == expected
    with database() as editor:
        editor.execute(
            text("UPDATE sources SET approval_status='rejected' WHERE id=:id"), {"id": source_id}
        )
        assert search_index._visible_tag_count(editor) == 0
        with database() as reader:
            assert search_index._visible_tag_count(reader) == 1
        editor.rollback()
        assert search_index._visible_tag_count(editor) == 1
    with database.begin() as session:
        rejected = Source(
            name="Rejected",
            feed_url="https://example.test/other",
            source_type="publisher",
            approval_status="rejected",
        )
        session.add(rejected)
        session.flush()
        rejected_id = rejected.id
        session.add(
            ArticleOrigin(
                article_id=article_id,
                source_id=rejected_id,
                entry_key="two",
                original_url="https://example.test/article",
            )
        )
    with database() as session:
        assert search_index._visible_tag_count(session) == 1
    with database.begin() as editor:
        editor.execute(
            text("UPDATE article_origins SET source_id=:source WHERE article_id=:article"),
            {"source": rejected_id, "article": article_id},
        )
    with database() as session:
        assert search_index._visible_tag_count(session) == 0
    with database.begin() as editor:
        editor.execute(
            text("UPDATE article_origins SET source_id=:source WHERE article_id=:article"),
            {"source": source_id, "article": article_id},
        )
    with database() as session:
        assert search_index._visible_tag_count(session) == 1
    with database.begin() as editor:
        editor.execute(text("DELETE FROM article_tags WHERE article_id=:id"), {"id": article_id})
    with database() as session:
        assert search_index._visible_tag_count(session) == 0
