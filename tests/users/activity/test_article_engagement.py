"""Article interactions are durable, deduplicated, private and visibility-gated."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import (
    ArticleLike,
    UserReadingDay,
    UserReadingEvent,
    utcnow,
)
from devfeed_user_api import engagement
from sqlalchemy import event, func, insert, select

from tests.support.user_analytics import direct_client as direct_client
from tests.support.user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.fixture
def interactions(direct_client, user_data):
    client, current, first, ids = direct_client
    return client, current, first, user_data[3], ids


def test_likes_are_idempotent_and_owned(interactions, database):
    client, current, first, second, ids = interactions
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/like"
    for _ in range(2):
        result = client.put(url, json={"liked": True})
        assert result.status_code == 200 and result.json()["likes"] == 1
        assert result.json()["liked"] is True
    current.user_id, current.subject = str(second), "user-b"
    assert client.put(url, json={"liked": True}).json()["likes"] == 2
    current.user_id, current.subject = str(first), "user-a"
    assert client.put(url, json={"liked": False}).json()["likes"] == 1
    assert client.put(url, json={"liked": False}).json()["likes"] == 1
    with database() as session:
        assert list(
            session.scalars(select(ArticleLike.user_id).where(ArticleLike.article_id == article_id))
        ) == [second]
    for title in ["Article 110", "Article 111", "Article 112", "Article 113"]:
        assert (
            client.put(f"/v1/user/articles/{ids[title]}/like", json={"liked": True}).status_code
            == 404
        )


def test_guest_cannot_create_reading_progress(interactions, database, monkeypatch):
    from devfeed_user_api import auth
    from devfeed_user_api.config import Settings

    settings = Settings(
        _env_file=None,
        base_url="http://testserver",
        cookie_secure=False,
        oidc_issuer_url="https://identity.example",
        oidc_client_id="test-client",
        oidc_organization_id="org-1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    client, _, _, _, ids = interactions
    client.app.dependency_overrides.pop(auth.require_user)
    response = client.post(
        f"/v1/user/articles/{ids['Article 000']}/open", headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 401
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 0


def test_user_opens_deduplicate_across_browsers_and_concurrent_calls(interactions, database):
    client, current, _, _, ids = interactions
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/open"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.post(url, headers={"Origin": "http://testserver"}), range(4))
        )
    assert all(response.status_code == 200 for response in responses)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 1


def test_daily_reads_dedupe_across_hours_and_roll_over_at_utc_midnight(
    interactions, database, monkeypatch
):
    client, current, first, _, ids = interactions
    instant = utcnow().replace(hour=22, minute=0, second=0, microsecond=0) - timedelta(days=2)
    monkeypatch.setattr(engagement, "utcnow", lambda: instant)
    url = f"/v1/user/articles/{ids['Article 000']}/open"
    headers = {"Origin": "http://testserver"}
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    assert client.post(url, headers=headers).status_code == 200
    first_day = instant.date()
    instant += timedelta(hours=1)
    assert client.post(url, headers=headers).status_code == 200
    instant += timedelta(hours=1)
    assert client.post(url, headers=headers).status_code == 200
    with database() as session:
        assert session.get(UserReadingDay, (first, first_day)).article_count == 1
        assert session.get(UserReadingDay, (first, instant.date())).article_count == 1


@pytest.mark.parametrize("limit", [1, 100])
def test_bulk_engagement_is_one_query(interactions, database, limit):
    client, _, first, _, ids = interactions
    with database.begin() as session:
        session.execute(insert(ArticleLike).values(article_id=ids["Article 000"], user_id=first))
        session.execute(insert(ArticleLike).values(article_id=ids["Article 110"], user_id=first))
    assert (
        client.post(
            f"/v1/user/articles/{ids['Article 001']}/open", headers={"Origin": "http://testserver"}
        ).status_code
        == 200
    )
    queries = []

    def count(*args):
        queries.append(args[2])

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        response = client.get(
            "/v1/user/engagement",
            params=[("article_id", str(value)) for value in list(ids.values())[:limit]],
        )
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    assert response.status_code == 200 and len(queries) == 1
    assert all(item["liked"] is False for item in response.json())


def test_unavailable_limiter_refuses_tracking_before_article_queries(interactions, monkeypatch):
    from types import SimpleNamespace

    from devfeed_user_api import open_limits
    from redis.exceptions import ConnectionError

    client, _, _, _, ids = interactions

    def unavailable(*args):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(open_limits, "get_redis", lambda: SimpleNamespace(eval=unavailable))
    monkeypatch.setattr(engagement, "public_article", lambda *args: pytest.fail("Queried article"))
    assert (
        client.post(
            f"/v1/user/articles/{ids['Article 000']}/open", headers={"Origin": "http://testserver"}
        ).status_code
        == 503
    )
