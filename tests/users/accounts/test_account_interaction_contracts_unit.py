"""Account interaction intent, click privacy, and service-error boundaries.

Supplied visibility/deduplication results are repository reads. SQL filtering,
hourly uniqueness, counters, reading-day transactions, and Redis atomicity are not emulated.
"""

import hashlib
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from devfeed_user_api import bookmarks, dependencies, engagement, oidc, open_limits
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.config import Settings
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert

from tests.api.test_feed_snapshot_contracts_unit import published_article
from tests.support.article_readiness import processing_case as processing_case
from tests.support.reader_context import stories as stories
from tests.support.reader_context import user

NOW = datetime(2026, 10, 22, 12, 34, tzinfo=UTC)


class FakeAnalyticsClient:
    def __init__(self):
        self.inserts = []

    async def query(self, statement, parameters=None):
        if "analytics_article_open_cutover" in statement:
            return [
                {
                    "entries": 1,
                    "earliest": NOW.replace(minute=0).isoformat(),
                    "latest": NOW.replace(minute=0).isoformat(),
                    "ready": 1,
                }
            ]
        if "analytics_article_open_baseline" in statement:
            return [{"article_id": str(identity), "opens": 4} for identity in parameters["ids"]]
        return []

    async def insert(self, table, rows):
        self.inserts.append((table, rows))


@pytest.fixture
def api(monkeypatch):
    owner, article_id = uuid.uuid4(), uuid.uuid4()
    user = UserIdentity(
        user_id=str(owner),
        issuer="https://identity.example",
        subject="reader",
        organization_id="readers",
        expires_at=4_102_444_800,
        csrf_token="test-csrf",
    )
    settings = Settings(_env_file=None, base_url="https://reader.example")
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    monkeypatch.setattr(engagement, "utcnow", lambda: NOW)
    limits, reading_day = Mock(), Mock()
    monkeypatch.setattr(engagement, "limit_open_requests", limits)
    monkeypatch.setattr(engagement, "record_reading_day", reading_day)
    projection = Mock(
        return_value=[{"article_id": article_id, "likes": 3, "liked": False, "bookmarked": False}]
    )
    monkeypatch.setattr(engagement, "engagement_rows", projection)
    session = Mock(spec=Session)
    session.scalar.return_value = article_id
    app = FastAPI()
    app.state.analytics_client = FakeAnalyticsClient()
    app.include_router(engagement.router)
    app.include_router(bookmarks.router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[engagement.optional_user] = lambda: None
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app, base_url=settings.base_url) as client:
        yield SimpleNamespace(
            owner=owner,
            article_id=article_id,
            user=user,
            settings=settings,
            session=session,
            client=client,
            app=app,
            limits=limits,
            reading_day=reading_day,
            projection=projection,
            analytics=app.state.analytics_client,
        )


@pytest.mark.parametrize("bookmarked", [False, True])
def test_bookmark_command_preserves_explicit_intent_without_returning_article_metadata(
    api, bookmarked
):
    if not bookmarked:
        api.session.scalar.side_effect = AssertionError(
            "Removal must remain available for withdrawn articles"
        )

    response = api.client.put(
        f"/v1/user/articles/{api.article_id}/bookmark", json={"bookmarked": bookmarked}
    )

    assert response.status_code == 200 and response.json() == {
        "article_id": str(api.article_id),
        "bookmarked": bookmarked,
    }
    command = api.session.execute.call_args.args[0]
    assert isinstance(command, Insert if bookmarked else Delete)
    parameters = command.compile().params
    assert api.owner in parameters.values() and api.article_id in parameters.values()
    api.session.commit.assert_called_once()


def test_new_bookmark_cannot_bypass_the_visible_article_read(api):
    api.session.scalar.return_value = None

    response = api.client.put(
        f"/v1/user/articles/{api.article_id}/bookmark", json={"bookmarked": True}
    )

    assert response.status_code == 404
    api.session.execute.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("liked", [False, True])
def test_like_commands_use_the_owned_identity_and_return_the_service_projection(api, liked):
    api.projection.return_value = [
        {"article_id": api.article_id, "likes": 3, "liked": liked, "bookmarked": True}
    ]

    response = api.client.put(f"/v1/user/articles/{api.article_id}/like", json={"liked": liked})

    assert response.status_code == 200 and response.json()["liked"] is liked
    assert response.json()["bookmarked"] is True
    command = api.session.execute.call_args.args[0]
    assert isinstance(command, Insert if liked else Delete)
    assert api.owner in command.compile().params.values()
    api.projection.assert_called_once_with(api.session, [api.article_id], api.user)
    api.session.commit.assert_called_once()


@pytest.mark.parametrize("extra", [False, True])
def test_saved_article_pages_anchor_the_cursor_at_the_last_returned_saved_date(api, extra):
    articles = [published_article(index) for index in ([4, 3, 2] if extra else [4, 3])]
    saved_at = NOW
    api.session.execute.return_value = [(article, saved_at) for article in articles]

    response = api.client.get("/v1/user/bookmarks", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(article.id) for article in articles[:2]]
    if extra:
        assert bookmarks.decode_cursor(body["next_cursor"]) == (saved_at, articles[1].id)
        next_articles = [articles[2], published_article(1)]
        api.session.execute.return_value = [(article, saved_at) for article in next_articles]
        continuation = api.client.get(
            "/v1/user/bookmarks", params={"limit": 2, "cursor": body["next_cursor"]}
        )
        assert continuation.status_code == 200 and continuation.json()["next_cursor"] is None
        assert [item["id"] for item in continuation.json()["items"]] == [
            str(article.id) for article in next_articles
        ]
    else:
        assert body["next_cursor"] is None
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize("recorded", [False, True])
def test_original_article_click_hashes_identity_and_forwards_only_new_open_intent(
    api, signed_in, recorded
):
    visitor = "v" * 43
    api.client.cookies.set(oidc.cookie_name(api.settings, "visitor"), visitor)
    if signed_in:
        api.app.dependency_overrides[engagement.optional_user] = lambda: api.user
    api.session.scalar.side_effect = [
        api.article_id,
        *([api.owner] if signed_in else []),
        api.article_id if recorded else None,
    ]

    response = api.client.post(
        f"/v1/user/articles/{api.article_id}/open", headers={"Origin": api.settings.base_url}
    )

    assert response.status_code == 200, response.text
    identity = "user:" + str(api.owner) if signed_in else "visitor:" + visitor
    digest = hashlib.sha256(identity.encode()).hexdigest()
    api.limits.assert_called_once_with(api.article_id, digest, anonymous=not signed_in)
    assert api.session.execute.call_count == 0
    assert len(api.analytics.inserts) == 1
    table, rows = api.analytics.inserts[0]
    assert table == "analytics_article_opens"
    assert rows[0]["article_id"] == str(api.article_id)
    assert rows[0]["viewer_key"] == digest
    if signed_in:
        api.reading_day.assert_called_once_with(api.session, api.owner, api.article_id, NOW)
    else:
        api.reading_day.assert_not_called()
    assert "set-cookie" not in response.headers
    assert visitor not in response.text and str(api.owner) not in response.text
    assert api.session.commit.call_count == int(signed_in)


def test_new_anonymous_click_receives_only_an_opaque_secure_visitor_cookie(api):
    response = api.client.post(
        f"/v1/user/articles/{api.article_id}/open", headers={"Origin": api.settings.base_url}
    )

    assert response.status_code == 200
    token = api.client.cookies.get(oidc.cookie_name(api.settings, "visitor"))
    assert engagement.TOKEN.fullmatch(token)
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=lax" in header
    assert "Max-Age=2592000" in header
    assert token not in response.text


@pytest.mark.parametrize("failure", ["origin", "budget", "unavailable"])
def test_invalid_origin_or_tracking_budget_fails_before_article_reads_and_writes(api, failure):
    if failure != "origin":
        api.limits.side_effect = HTTPException(
            429 if failure == "budget" else 503, "Tracking unavailable"
        )

    response = api.client.post(
        f"/v1/user/articles/{api.article_id}/open",
        headers={
            "Origin": "https://other.example" if failure == "origin" else api.settings.base_url
        },
    )

    assert response.status_code == {"origin": 403, "budget": 429, "unavailable": 503}[failure]
    api.session.scalar.assert_not_called()
    api.session.execute.assert_not_called()
    api.session.commit.assert_not_called()
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("status", [401, 403, 503])
def test_optional_user_treats_only_expired_authority_as_anonymous(monkeypatch, status):
    settings = Settings(_env_file=None, base_url="https://reader.example")
    token = "a" * 43
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{oidc.cookie_name(settings, 'session')}={token}".encode())],
        }
    )
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    validator = Mock(side_effect=HTTPException(status, "Session unavailable"))
    monkeypatch.setattr(engagement, "require_user", validator)

    if status == 401:
        assert engagement.optional_user(request) is None
    else:
        with pytest.raises(HTTPException) as error:
            engagement.optional_user(request)
        assert error.value.status_code == status
    validator.assert_called_once_with(request)


@pytest.mark.parametrize("token", [None, "malformed"])
def test_missing_or_invalid_cookie_does_not_attempt_optional_authentication(monkeypatch, token):
    settings = Settings(_env_file=None, base_url="https://reader.example")
    headers = (
        []
        if token is None
        else [(b"cookie", f"{oidc.cookie_name(settings, 'session')}={token}".encode())]
    )
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    validator = Mock()
    monkeypatch.setattr(engagement, "require_user", validator)

    assert engagement.optional_user(Request({"type": "http", "headers": headers})) is None

    validator.assert_not_called()


@pytest.mark.parametrize("anonymous", [False, True])
def test_open_budget_command_keeps_shared_anonymous_caps_separate_from_owned_viewers(
    monkeypatch, anonymous
):
    consume = Mock(return_value=0)
    redis = object()
    monkeypatch.setattr(open_limits, "consume_rate_limits", consume)
    monkeypatch.setattr(open_limits, "get_redis", lambda: redis)
    article_id, digest = uuid.uuid4(), hashlib.sha256(b"viewer").hexdigest()

    open_limits.limit_open_requests(article_id, digest, anonymous=anonymous)

    selected, budgets = consume.call_args.args
    assert selected is redis
    assert [(budget.limit, budget.window) for budget in budgets] == (
        [(30, 60), (180, 3600), (20, 60), (100, 3600), (120, 60), (1000, 3600)]
        if anonymous
        else [(30, 60), (180, 3600)]
    )
    assert all(digest in budget.key for budget in budgets[:2])


@pytest.mark.parametrize("failure", ["throttled", "offline"])
def test_open_budget_failures_are_actionable_without_dependency_details(monkeypatch, failure):
    consume = Mock(
        return_value=17, side_effect=RedisError("private-secret") if failure == "offline" else None
    )
    monkeypatch.setattr(open_limits, "consume_rate_limits", consume)
    monkeypatch.setattr(open_limits, "get_redis", lambda: object())

    with pytest.raises(HTTPException) as error:
        open_limits.limit_open_requests(uuid.uuid4(), "hashed-viewer", anonymous=True)

    assert error.value.status_code == (503 if failure == "offline" else 429)
    if failure == "throttled":
        assert error.value.headers == {"Retry-After": "17"}
    assert "private-secret" not in error.value.detail


@contextmanager
def read_api(viewer=None):
    app, session = FastAPI(), Mock(spec=Session)
    app.state.analytics_client = FakeAnalyticsClient()
    app.include_router(engagement.router)
    app.dependency_overrides[engagement.optional_user] = lambda: viewer
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session)


@pytest.mark.parametrize("signed_in", [False, True])
def test_engagement_projection_returns_counts_and_owned_flags_without_personal_identifiers(
    signed_in,
):
    viewer, identifier = user() if signed_in else None, uuid.uuid4()
    selected = {
        "article_id": identifier,
        "likes": 3,
        "liked": signed_in,
        "bookmarked": signed_in,
    }
    with read_api(viewer) as api:
        api.session.execute.return_value.mappings.return_value = [selected]
        response = api.client.get("/v1/user/engagement", params={"article_id": str(identifier)})
        assert response.status_code == 200
        assert response.json() == [{**selected, "article_id": str(identifier), "opens": 4}]
        assert set(response.json()[0]) == set(engagement.EngagementOut.model_fields)
        api.session.execute.assert_called_once()
        api.session.commit.assert_not_called()
        api.session.add.assert_not_called()


@pytest.mark.parametrize(
    "offset,empty,next_cursor",
    [(0, False, "1"), (999999, False, "1000000"), (1000000, False, None), (0, True, None)],
)
def test_trending_lookahead_returns_only_the_requested_page_and_stops_at_the_supported_cursor_bound(
    stories, offset, empty, next_cursor
):
    from devfeed_core.schemas import ArticleOut, FeedPage

    page = FeedPage(
        items=[] if empty else ArticleOut.from_articles([stories[0]]),
        next_cursor=str(offset + 1) if not empty and offset < 1_000_000 else None,
    )
    with (
        read_api() as api,
        patch.object(engagement, "trending_page", AsyncMock(return_value=page)) as ranked,
    ):
        response = api.client.get("/v1/user/trending", params={"cursor": offset, "limit": 1})
        assert response.status_code == 200, response.text
        output = response.json()
        assert [item["id"] for item in output["items"]] == ([] if empty else [str(stories[0].id)])
        assert output["next_cursor"] == next_cursor
        if not empty:
            assert "classification_provenance" not in output["items"][0]
        ranked.assert_awaited_once()
        api.session.commit.assert_not_called()
