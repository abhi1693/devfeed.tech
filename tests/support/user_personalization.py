"""Reader identities, visibility facts, and personalized API clients."""

import time
import uuid

import pytest
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
)
from devfeed_user_api.accounts import save_user
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import insert


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
                    language="en",
                    publication_status=("unpublished" if index in {110, 111} else "published"),
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
        user_id=str(first),
        **identity,
        expires_at=int(time.time()) + 3600,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: current
    with TestClient(app) as client:
        yield client, current, first, second, topics
