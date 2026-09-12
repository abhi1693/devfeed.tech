import uuid

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    SearchEvent,
    Source,
    Tag,
    Topic,
)
from devfeed_core.search_engine import KINDS, SearchUnavailable, Typesense
from devfeed_core.search_index import sync_batch
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update

pytestmark = pytest.mark.integration


def seed(database):
    with database.begin() as session:
        source = Source(
            name="Kubernetes Journal",
            feed_url="https://kube.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        topic = Topic(
            name="Kubernetes",
            slug="kubernetes",
            kind="technology",
            status="active",
            aliases=["k8s"],
        )
        tag = Tag(name="Kubernetes", slug="kubernetes", aliases=["k8s"])
        session.add_all([source, topic, tag])
        session.flush()
        articles = [
            Article(
                title=title,
                canonical_url=f"https://kube.example/{i}",
                url_hash=str(i),
                review_status="approved",
                publication_status="published" if i != 2 else "unpublished",
            )
            for i, title in enumerate(
                [
                    "Understanding Kubernetes Networking",
                    "A container deployment guide",
                    "Private Kubernetes draft",
                ]
            )
        ]
        session.add_all(articles)
        session.flush()
        for article in articles:
            session.add_all(
                [
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=source.id,
                        entry_key=str(article.id),
                        original_url=article.canonical_url,
                    ),
                    ArticleTopic(
                        article_id=article.id,
                        topic_id=topic.id,
                        role="primary",
                        relevance=1.0,
                        evidence="Test classification",
                    ),
                    ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"),
                ]
            )
        return source.id, topic.id, tag.id, [article.id for article in articles]


def drain(database, engine):
    for _ in range(30):
        if not sync_batch(database, engine):
            return
    pytest.fail("Search outbox did not drain")


def test_federated_typo_alias_relationship_search_and_publication_gates(
    client, database, search_engine
):
    source, topic, tag, articles = seed(database)
    drain(database, search_engine)
    result = client.get("/v1/search?q=kuberentes").json()["sections"]
    assert set(result) == set(KINDS)
    assert result["articles"]["items"][0]["id"] == str(articles[0])
    assert {item["id"] for item in result["articles"]["items"]} == set(map(str, articles[:2]))
    assert result["topics"]["items"][0]["href"] == "/topics/kubernetes"
    assert result["tags"]["items"][0]["href"] == "/tags/kubernetes"
    assert result["sources"]["items"][0]["id"] == str(source)
    alias = client.get("/v1/search?q=k8s").json()["sections"]
    assert len(alias["articles"]["items"]) == 2
    with database.begin() as session:
        session.execute(
            update(Article)
            .where(Article.id == articles[0])
            .values(publication_status="unpublished")
        )
    # Search still contains an old hit: authoritative hydration must exclude it now.
    current = client.get("/v1/search?q=kubernetes").json()["sections"]["articles"]["items"]
    assert [item["id"] for item in current] == [str(articles[1])]
    drain(database, search_engine)
    assert search_engine.search("network")["articles"]["found"] == 0
    with database.begin() as session:
        session.execute(
            update(Source).where(Source.id == source).values(approval_status="rejected")
        )
    hidden = client.get("/v1/search?q=kubernetes").json()["sections"]
    assert all(not value["items"] for value in hidden.values())
    drain(database, search_engine)
    assert all(value["found"] == 0 for value in search_engine.search("kubernetes").values())


def test_bulk_link_deletions_and_failed_imports_keep_durable_work(database, search_engine):
    source, topic, tag, articles = seed(database)
    with database.begin() as session:
        session.execute(delete(ArticleTag).where(ArticleTag.tag_id == tag))
        session.execute(delete(Tag).where(Tag.id == tag))

    class Unavailable:
        def sync(self, *args):
            raise SearchUnavailable("offline")

    with pytest.raises(SearchUnavailable):
        sync_batch(database, Unavailable())
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SearchEvent)) > 0
    drain(database, search_engine)
    assert search_engine.search("kubernetes")["tags"]["found"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SearchEvent)) == 0


def test_polling_metadata_does_not_flood_index_and_rollback_has_no_events(database, search_engine):
    source, *_ = seed(database)
    drain(database, search_engine)
    with database.begin() as session:
        session.execute(update(Source).where(Source.id == source).values(consecutive_failures=1))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SearchEvent)) == 0
        session.execute(update(Source).where(Source.id == source).values(name="Rolled back"))
        session.rollback()
        assert session.scalar(select(func.count()).select_from(SearchEvent)) == 0


def test_setup_is_idempotent_and_query_key_cannot_write(database, search_engine):
    from devfeed_cli.search_commands import setup

    settings = get_settings()
    settings.search_query_key = SecretStr(uuid.uuid4().hex)
    setup()
    setup()
    reader = Typesense()
    assert all(result["found"] == 0 for result in reader.search("python").values())
    with pytest.raises(SearchUnavailable):
        reader.request("POST", "/collections", data={"name": "forbidden", "fields": []})
