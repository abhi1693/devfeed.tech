"""Topic count reuse respects roles, catalog status and raw SQL changes."""

import pytest
from devfeed_core import search_index
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_topic_count_reuses_snapshot_and_role_status_revocation_are_fresh(database, monkeypatch):
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
        topic = Topic(name="Topic", slug="topic", kind="technology", status="active")
        session.add_all([source, article, topic])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="one",
                original_url=article.canonical_url,
            )
        )
        session.add(
            ArticleTopic(
                article_id=article.id,
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence="Example",
            )
        )
        article_id, topic_id = article.id, topic.id
    query = search_index._visible_topic_count_query
    calls = []

    def counted():
        calls.append(True)
        return query()

    monkeypatch.setattr(search_index, "_visible_topic_count_query", counted)
    for _ in range(5):
        with database() as session:
            assert search_index._visible_topic_count(session) == 1
    assert len(calls) == 1
    for role, expected in (("incidental", 0), ("supporting", 1), ("comparison", 0), ("primary", 1)):
        with database.begin() as editor:
            editor.execute(
                text("UPDATE article_topics SET role=:role WHERE article_id=:id"),
                {"role": role, "id": article_id},
            )
        with database() as session:
            assert search_index._visible_topic_count(session) == expected
    for status, expected in (("rejected", 0), ("active", 1)):
        with database.begin() as editor:
            editor.execute(
                text("UPDATE topics SET status=:status WHERE id=:id"),
                {"status": status, "id": topic_id},
            )
        with database() as session:
            assert search_index._visible_topic_count(session) == expected
    with database.begin() as editor:
        editor.execute(text("DELETE FROM article_topics WHERE article_id=:id"), {"id": article_id})
    with database() as session:
        assert search_index._visible_topic_count(session) == 0
