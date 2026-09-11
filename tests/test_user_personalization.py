"""Real PostgreSQL ownership, persistence, visibility and bounded feed queries."""

import time
import uuid

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    UserAccount,
    UserTopic,
)
from devfeed_user_api.accounts import save_user
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select

pytestmark = pytest.mark.integration


@pytest.fixture
def user_data(database):
    identity = {
        "issuer": "https://identity.example",
        "subject": "user-a",
        "organization_id": "org-1",
        "name": "User",
        "email": "same@example.test",
    }
    first = uuid.UUID(save_user(identity))
    second = uuid.UUID(save_user({**identity, "subject": "user-b"}))
    topics = [uuid.uuid4() for _ in range(3)]
    sources = [uuid.uuid4(), uuid.uuid4()]
    with database.begin() as session:
        for index, topic_id in enumerate(topics):
            session.execute(
                insert(Topic.__table__).values(
                    id=topic_id,
                    name=f"Topic {index}",
                    slug=f"topic-{index}",
                    kind="technology",
                    status="active" if index < 2 else "proposed",
                )
            )
        for index, source_id in enumerate(sources):
            session.execute(
                insert(Source.__table__).values(
                    id=source_id,
                    name=f"Source {index}",
                    source_type="publisher",
                    feed_url=f"https://example.test/{index}/rss",
                    approval_status="approved" if index == 0 else "rejected",
                )
            )
        # Mixed visibility, topic assignment and source moderation. Only the
        # first 110 are eligible for this user; two topic links must not duplicate rows.
        for index in range(117):
            article_id = uuid.uuid4()
            url = f"https://example.test/articles/{index}"
            session.execute(
                insert(Article.__table__).values(
                    id=article_id,
                    canonical_url=url,
                    url_hash=article_id.hex,
                    title=f"Article {index:03}",
                    publication_status="unpublished" if index in {110, 111} else "published",
                    review_status="pending" if index == 111 else "approved",
                )
            )
            if index != 112:
                session.execute(
                    insert(ArticleOrigin.__table__).values(
                        article_id=article_id,
                        source_id=sources[1 if index == 113 else 0],
                        entry_key=str(index),
                        original_url=url,
                    )
                )
            session.execute(
                insert(ArticleTopic.__table__).values(
                    article_id=article_id,
                    topic_id=topics[1 if index == 114 else 2 if index == 115 else 0],
                    role="incidental" if index == 116 else "primary",
                    relevance=1,
                    evidence="test",
                )
            )
            if index < 110:
                session.execute(
                    insert(ArticleTopic.__table__).values(
                        article_id=article_id,
                        topic_id=topics[1],
                        role="supporting",
                        relevance=1,
                        evidence="test",
                    )
                )
    current = UserIdentity(
        user_id=str(first), **identity, expires_at=int(time.time()) + 3600, csrf_token="test"
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: current
    with TestClient(app) as client:
        yield client, current, first, second, topics


def test_identity_upsert_uses_issuer_subject_and_preserves_preferences(database):
    identity = {
        "issuer": "https://identity.example",
        "subject": "user-a",
        "organization_id": "org-1",
        "name": "First",
        "email": "shared@example.test",
    }
    account_id = save_user(identity)
    assert save_user({**identity, "name": "Updated"}) == account_id
    assert save_user({**identity, "subject": "user-b"}) != account_id
    assert save_user({**identity, "issuer": "https://another.example"}) != account_id
    with database() as session:
        account = session.get(UserAccount, uuid.UUID(account_id))
        assert account.name == "Updated"


def test_preferences_are_owned_atomic_and_bounded(user_data, database):
    client, current, first, second, topics = user_data
    assert client.get("/v1/user/preferences").json() == {"topic_ids": []}
    response = client.put(
        "/v1/user/preferences", json={"topic_ids": [str(topics[0]), str(topics[0])]}
    )
    assert response.status_code == 200
    assert response.json() == {"topic_ids": [str(topics[0])]}
    for payload in (
        {"topic_ids": [str(topics[2])]},
        {"topic_ids": [str(uuid.uuid4())]},
        {"topic_ids": [str(topics[0])] * 101},
        {"topic_ids": [], "user_id": str(second)},
    ):
        assert client.put("/v1/user/preferences", json=payload).status_code == 422
    assert client.get("/v1/user/preferences").json() == {"topic_ids": [str(topics[0])]}
    current.user_id = str(second)
    current.subject = "user-b"
    assert client.get("/v1/user/preferences").json() == {"topic_ids": []}
    assert client.get("/v1/user/feed").json()["items"] == []
    assert (
        client.put("/v1/user/preferences", json={"topic_ids": [str(topics[1])]}).status_code == 200
    )
    current.user_id, current.subject = str(first), "user-a"
    assert client.put("/v1/user/preferences", json={"topic_ids": []}).status_code == 200
    with database() as session:
        assert list(
            session.scalars(select(UserTopic.topic_id).where(UserTopic.user_id == second))
        ) == [topics[1]]
        assert (
            list(session.scalars(select(UserTopic.topic_id).where(UserTopic.user_id == first)))
            == []
        )


@pytest.mark.parametrize("limit", [1, 100])
def test_personalized_feed_visibility_pagination_and_constant_query_budget(user_data, limit):
    client, _, _, _, topics = user_data
    assert (
        client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]}).status_code == 200
    )
    statements = []

    def counted(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", counted)
    try:
        response = client.get(f"/v1/user/feed?limit={limit}")
    finally:
        event.remove(get_engine(), "before_cursor_execute", counted)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(statements) == 4
    data = response.json()
    seen = [item["id"] for item in data["items"]]
    titles = [item["title"] for item in data["items"]]
    while data["next_cursor"]:
        data = client.get(
            "/v1/user/feed", params={"limit": 100, "cursor": data["next_cursor"]}
        ).json()
        seen.extend(item["id"] for item in data["items"])
        titles.extend(item["title"] for item in data["items"])
    assert len(seen) == len(set(seen)) == 110
    assert all(int(title.split()[-1]) < 110 for title in titles)
    assert client.get("/v1/user/feed?cursor=invalid").status_code == 422
    # Following both eligible topics does not duplicate shared articles.
    client.put("/v1/user/preferences", json={"topic_ids": [str(t) for t in topics[:2]]})
    data = client.get("/v1/user/feed?limit=100").json()
    assert len({item["id"] for item in data["items"]}) == 100


def test_user_rename_preserves_existing_accounts_topics_and_likes(user_data, database):
    from devfeed_core.models import ArticleLike
    from sqlalchemy import text
    from test_topics_migration import migration

    client, _, first, _, topics = user_data
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    with database.begin() as session:
        article_id = session.scalar(select(Article.id).limit(1))
        session.execute(insert(ArticleLike).values(article_id=article_id, user_id=first))
    with get_engine().connect() as connection, connection.begin():
        rename = migration("0016_user_accounts", connection)
        rename.downgrade()
        assert (
            connection.scalar(
                text("SELECT count(*) FROM reader_accounts WHERE id = :id"), {"id": first}
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM reader_topics WHERE reader_id = :id"), {"id": first}
            )
            == 1
        )
        rename.upgrade()
        assert (
            connection.scalar(
                text("SELECT count(*) FROM user_accounts WHERE id = :id"), {"id": first}
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM user_topics WHERE user_id = :id"), {"id": first}
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM article_likes WHERE user_id = :id"), {"id": first}
            )
            == 1
        )
