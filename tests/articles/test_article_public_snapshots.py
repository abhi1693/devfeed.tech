"""One public response cannot mix an approved projection across atomic swaps."""

import uuid
from contextlib import asynccontextmanager

import pytest
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine, read_session_factory
from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserAccount,
    UserMustRead,
    utcnow,
)
from devfeed_user_api import bookmarks, engagement, must_reads
from devfeed_user_api.auth import UserIdentity, require_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_public_read_factory_keeps_write_sessions_and_the_existing_pool_independent(database):
    readonly = read_session_factory()
    assert readonly.kw["bind"].pool is database.kw["bind"].pool
    with readonly() as session:
        assert session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        with pytest.raises(DBAPIError) as error:
            session.execute(
                update(Article).where(Article.id == uuid.uuid4()).values(title="Denied")
            )
        assert error.value.orig.sqlstate == "25006"
    with database() as session:
        assert session.scalar(text("SHOW transaction_isolation")) == "read committed"
        assert session.scalar(text("SHOW transaction_read_only")) == "off"
        session.execute(update(Article).where(Article.id == uuid.uuid4()).values(title="Allowed"))


@pytest.mark.parametrize(
    ("surface", "cached", "budget"),
    [
        ("detail", False, 4),
        ("detail", True, 4),
        ("feed", False, 4),
        ("feed", True, 4),
        ("oldest", False, 4),
        ("most_liked", False, 4),
        ("diverse", False, 6),
        ("bookmarks", False, 4),
        ("trending", False, 5),
        ("must_reads", False, 9),
    ],
)
def test_atomic_replacement_keeps_article_fields_and_associations_on_one_snapshot(
    database, client, monkeypatch, surface, cached, budget
):
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_enabled", cached)
    cache.close_cache()
    owner = uuid.uuid4()
    with database.begin() as session:
        source = Source(
            name="Snapshot publisher",
            feed_url="https://publisher.example/snapshot/rss",
            source_type="publisher",
            approval_status="approved",
        )
        old_tag = Tag(name="Old tag", slug="old-tag")
        new_tag = Tag(name="New tag", slug="new-tag")
        old_topic = Topic(name="Old topic", slug="old-topic", kind="technology", status="active")
        new_topic = Topic(name="New topic", slug="new-topic", kind="technology", status="active")
        article = Article(
            canonical_url="https://publisher.example/snapshot",
            url_hash="a" * 64,
            title="Old approved headline",
            summary="Old approved summary",
            image_url="https://publisher.example/old.png",
            review_status="approved",
            publication_status="published",
            language="en",
            tags=[old_tag],
            origins=[
                ArticleOrigin(
                    source=source,
                    entry_key="snapshot",
                    original_url="https://publisher.example/snapshot",
                )
            ],
            topic_links=[
                ArticleTopic(
                    topic=old_topic, role="primary", relevance=1, evidence="Approved topic"
                )
            ],
        )
        session.add_all(
            [
                article,
                new_tag,
                new_topic,
                UserAccount(
                    id=owner,
                    issuer="https://identity.example",
                    subject="snapshot-reader",
                    organization_id="readers",
                ),
            ]
        )
        session.flush()
        session.add(ArticleBookmark(user_id=owner, article_id=article.id))
        session.add(
            UserMustRead(
                user_id=owner,
                selection_date=utcnow().date(),
                timezone="UTC",
                picks=[{"id": str(article.id), "reason": "Saved approved pick"}],
            )
        )
        identifier, slug, tag_id, topic_id = article.id, article.slug, new_tag.id, new_topic.id
        source_id = source.id

    identity = UserIdentity(
        user_id=str(owner),
        issuer="https://identity.example",
        subject="snapshot-reader",
        organization_id="readers",
        expires_at=4_102_444_800,
        csrf_token="snapshot-csrf",
    )
    checked_out = set()

    class Opens:
        def __init__(self, _client):
            self.client = self

        @asynccontextmanager
        async def external_query_slot(self):
            yield

        async def cutover(self):
            return None

        async def trending(self, _since, _metadata, *, limit, cursor):
            # No request-owned PostgreSQL connection crosses the external query.
            assert not checked_out
            return [{"article_id": str(identifier)}]

    monkeypatch.setattr(engagement, "ArticleOpenAnalytics", Opens)
    app = FastAPI()
    app.state.analytics_client = object()
    app.include_router(bookmarks.router)
    app.include_router(engagement.router)
    app.include_router(must_reads.router)
    app.dependency_overrides[require_user] = lambda: identity
    paths = {
        "detail": f"/v1/articles/{slug}",
        "feed": "/v1/feed?limit=100",
        "oldest": "/v1/feed?sort=oldest&limit=100",
        "most_liked": "/v1/feed?sort=most_liked&limit=100",
        "diverse": "/v1/feed?diverse=true&limit=100",
        "bookmarks": "/v1/user/bookmarks?limit=100",
        "trending": "/v1/user/trending?limit=100",
        "must_reads": "/v1/user/must-reads?timezone=UTC",
    }
    statements = []
    replaced = []

    def replace_projection():
        with database.begin() as writer:
            writer.info["snapshot_writer"] = True
            writer.connection(execution_options={"snapshot_writer": True})
            writer.execute(
                update(Article)
                .where(Article.id == identifier)
                .values(
                    title="New approved headline",
                    summary="New approved summary",
                    image_url="https://publisher.example/new.png",
                )
            )
            writer.execute(delete(ArticleTag).where(ArticleTag.article_id == identifier))
            writer.execute(insert(ArticleTag).values(article_id=identifier, tag_id=tag_id))
            writer.execute(delete(ArticleTopic).where(ArticleTopic.article_id == identifier))
            writer.execute(
                insert(ArticleTopic).values(
                    article_id=identifier,
                    topic_id=topic_id,
                    role="primary",
                    relevance=1,
                    evidence="New approved topic",
                )
            )

    def replace_before_relationships(state):
        if (
            replaced
            or state.session.info.get("snapshot_writer")
            or not state.is_relationship_load
            or state.bind_mapper.class_ not in {Tag, ArticleOrigin, ArticleTopic}
        ):
            return
        replaced.append(True)
        if surface == "must_reads":
            # Follow promotion's Source -> Article order. The account transaction
            # has already fenced these rows, so promotion waits only for this
            # bounded response instead of changing associations underneath it.
            with database.begin() as writer:
                writer.info["snapshot_writer"] = True
                writer.connection(execution_options={"snapshot_writer": True})
                writer.scalar(
                    select(Source.id)
                    .where(Source.id == source_id)
                    .with_for_update(read=True, of=Source)
                )
                with pytest.raises(DBAPIError) as error:
                    writer.scalar(
                        select(Article.id)
                        .where(Article.id == identifier)
                        .with_for_update(nowait=True, of=Article)
                    )
                assert error.value.orig.sqlstate == "55P03"
                writer.rollback()
            return
        replace_projection()

    def count(_connection, _cursor, statement, _parameters, context, _many):
        if not context.execution_options.get("snapshot_writer"):
            statements.append(statement)

    def checkout(_connection, record, _proxy):
        checked_out.add(id(record))

    def checkin(_connection, record):
        checked_out.discard(id(record))

    engine = get_engine()
    event.listen(Session, "do_orm_execute", replace_before_relationships)
    event.listen(engine, "before_cursor_execute", count)
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    try:
        with TestClient(app) as user_client:
            http = user_client if surface in {"bookmarks", "trending", "must_reads"} else client
            response = http.get(paths[surface])
            assert response.status_code == 200, response.text
            before = response.json() if surface == "detail" else response.json()["items"][0]
            assert replaced and len(statements) == budget
            assert before["title"] == "Old approved headline"
            assert before["summary"] == "Old approved summary"
            assert before["image_url"] == "https://publisher.example/old.png"
            assert before["tags"] == ["old-tag"]
            assert [topic["slug"] for topic in before["topics"]] == ["old-topic"]
            if surface == "must_reads":
                # The request committed its existing pick transaction and released
                # both guards; a normal promotion transaction can now proceed.
                replace_projection()
            # An invalidated cache owner cannot publish its earlier snapshot into
            # the new generation; a subsequent request observes the committed swap.
            after_response = http.get(paths[surface])
            assert after_response.status_code == 200
            after = (
                after_response.json() if surface == "detail" else after_response.json()["items"][0]
            )
            assert after["title"] == "New approved headline"
            assert after["tags"] == ["new-tag"]
            assert [topic["slug"] for topic in after["topics"]] == ["new-topic"]
            if cached:
                assert response.headers["x-cache"] == after_response.headers["x-cache"] == "MISS"
                assert http.get(paths[surface]).headers["x-cache"] == "HIT"
    finally:
        event.remove(Session, "do_orm_execute", replace_before_relationships)
        event.remove(engine, "before_cursor_execute", count)
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
        cache.close_cache()
