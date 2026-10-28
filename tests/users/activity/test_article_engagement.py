"""Article interactions are durable, deduplicated, private and visibility-gated."""

import json
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
from devfeed_user_api.config import Settings
from sqlalchemy import event, func, insert, select, update

from tests.support.user_analytics import direct_client as direct_client
from tests.support.user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.fixture
def interactions(direct_client, user_data):
    client, current, first, ids, _ = direct_client
    return client, current, first, user_data[3], ids


def open_rows(client, article_id):
    return client.portal.call(
        client.app.state.analytics_client.query,
        "SELECT viewer_key FROM analytics_article_opens FINAL WHERE article_id={article:UUID}",
        {"article": str(article_id)},
    )


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


def test_anonymous_opens_deduplicate_and_retain_only_opaque_viewer_keys(interactions, database):
    client, _, _, _, ids = interactions
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/open"
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    response = client.post(url, headers={"Origin": "http://testserver"})
    assert response.status_code == 200 and response.json()["opens"] == 1
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    token = client.cookies.get("devfeed_user_visitor")
    assert client.post(url, headers={"Origin": "http://testserver"}).json()["opens"] == 1
    rows = open_rows(client, article_id)
    assert len(rows) == 1
    assert len(rows[0]["viewer_key"]) == 64 and token not in rows[0]["viewer_key"]
    client.cookies.clear()
    assert client.post(url, headers={"Origin": "http://testserver"}).json()["opens"] == 2
    assert (
        client.post(
            f"/v1/user/articles/{ids['Article 113']}/open",
            headers={"Origin": "http://testserver"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize("session_state", ["absent", "malformed", "revoked", "expired"])
def test_anonymous_clicks_use_real_auth_dependency_and_preserve_abuse_limits(
    interactions, database, monkeypatch, session_state
):
    from devfeed_user_api import auth, oidc
    from devfeed_user_api.dependencies import get_redis

    client, current, _, _, ids = interactions
    settings = Settings(
        _env_file=None,
        base_url="http://testserver",
        cookie_secure=False,
        oidc_issuer_url="https://identity.example",
        oidc_client_id="test-client",
        oidc_organization_id="org-1",
    )
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    client.app.dependency_overrides.clear()
    if session_state != "absent":
        token = "malformed" if session_state == "malformed" else "s" * 43
        client.cookies.set("devfeed_user_session", token)
        if session_state == "expired":
            record = {**current.model_dump(), "expires_at": 0, "policy": oidc.policy_key(settings)}
            get_redis().set(auth.key("session", token), json.dumps(record), ex=60)
    article_id = ids["Article 000"]
    path = f"/v1/user/articles/{article_id}/open"
    headers = {"Origin": "http://testserver"}
    response = client.post(path, headers=headers)
    assert response.status_code == 200 and response.json()["opens"] == 1
    assert client.cookies.get("devfeed_user_visitor")
    assert client.post(path, headers=headers).json()["opens"] == 1
    assert client.get("/v1/user/engagement", params={"article_id": str(article_id)}).json() == [
        {
            "article_id": str(article_id),
            "opens": 1,
            "likes": 0,
            "liked": False,
            "bookmarked": False,
        }
    ]
    # The fallback never grants access to authenticated mutations.
    assert (
        client.put(
            f"/v1/user/articles/{article_id}/like", json={"liked": True}, headers=headers
        ).status_code
        == 401
    )
    for _ in range(18):
        assert client.post(path, headers=headers).status_code == 200
    limited = client.post(path, headers=headers)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert len(open_rows(client, article_id)) == 1


@pytest.mark.parametrize("status", [403, 503])
def test_optional_identity_preserves_csrf_and_dependency_errors(interactions, monkeypatch, status):
    from fastapi import HTTPException

    client, _, _, _, ids = interactions
    client.app.dependency_overrides.pop(engagement.optional_user)
    client.cookies.set("devfeed_user_session", "s" * 43)

    def forbidden(_request):
        raise HTTPException(status, "Session unavailable or forbidden")

    monkeypatch.setattr(engagement, "require_user", forbidden)
    monkeypatch.setattr(
        engagement, "limit_open_requests", lambda *args, **kwargs: pytest.fail("Charged budget")
    )
    result = client.post(
        f"/v1/user/articles/{ids['Article 000']}/open", headers={"Origin": "http://testserver"}
    )
    assert result.status_code == status


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
    assert len(open_rows(client, article_id)) == 1
    assert (
        client.get("/v1/user/engagement", params={"article_id": str(article_id)}).json()[0]["opens"]
        == 1
    )
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
    assert client.post(url, headers=headers).status_code == 200
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 0
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
def test_bulk_engagement_is_one_query_and_trending_excludes_unpublished(
    interactions, database, limit
):
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
    trending = client.get("/v1/user/trending").json()["items"]
    assert [item["id"] for item in trending] == [str(ids["Article 000"]), str(ids["Article 001"])]
    with database.begin() as session:
        session.execute(update(ArticleLike).values(created_at=utcnow() - timedelta(days=8)))
    assert [item["id"] for item in client.get("/v1/user/trending").json()["items"]] == [
        str(ids["Article 001"])
    ]


def test_cookie_rotation_cannot_bypass_shared_open_limit(interactions, database):
    client, _, _, _, ids = interactions
    path = f"/v1/user/articles/{ids['Article 000']}/open"
    for _ in range(20):
        client.cookies.clear()
        assert client.post(path, headers={"Origin": "http://testserver"}).status_code == 200
    client.cookies.clear()
    response = client.post(path, headers={"Origin": "http://testserver"})
    assert response.status_code == 429 and 0 < int(response.headers["retry-after"]) <= 60
    assert len(open_rows(client, ids["Article 000"])) == 20
    assert (
        client.get("/v1/user/engagement", params={"article_id": str(ids["Article 000"])}).json()[0][
            "opens"
        ]
        == 20
    )


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


def test_trending_paginates_real_activity_and_rejects_invalid_cursors(interactions, database):
    client, _, first, _, ids = interactions
    names = [f"Article {index:03}" for index in range(7)]
    with database.begin() as session:
        for name in names:
            session.add(ArticleLike(article_id=ids[name], user_id=first))
    expected = [item["id"] for item in client.get("/v1/user/trending?limit=100").json()["items"]]
    seen = []
    cursor = "0"
    while cursor is not None:
        response = client.get("/v1/user/trending", params={"limit": 3, "cursor": cursor})
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        assert len(page["items"]) <= 3
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert seen == expected and len(seen) == len(set(seen)) == 7
    assert client.get("/v1/user/trending?cursor=100").json() == {"items": [], "next_cursor": None}
    for invalid in ("-1", "1000001", "oops"):
        assert client.get("/v1/user/trending", params={"cursor": invalid}).status_code == 422
