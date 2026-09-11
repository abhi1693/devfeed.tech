"""Saved display preferences are isolated by the authenticated user identity."""

import pytest
from devfeed_core.models import UserAccount
from devfeed_core.user_settings import FeedSettings
from devfeed_user_api.accounts import save_user
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration
PATH = "/v1/user/settings/feed"
DEFAULTS = FeedSettings().model_dump()


def test_preferences_persist_and_do_not_change_other_account_settings(user_data, database):
    client, current, first, second, _ = user_data
    assert client.get(PATH).json() == DEFAULTS
    client.put("/v1/user/settings/profile", json={"display_name": "Python Fan"})
    saved = {**DEFAULTS, "view": "compact"}
    response = client.put(PATH, json=saved)
    assert response.status_code == 200 and response.json() == saved
    save_user(
        {
            "issuer": current.issuer,
            "subject": current.subject,
            "organization_id": current.organization_id,
            "name": "New provider name",
            "email": "new@example.test",
        }
    )
    assert client.get(PATH).json() == saved
    with database() as session:
        assert session.get(UserAccount, first).profile["display_name"] == "Python Fan"
        assert session.get(UserAccount, second).feed_settings == {}
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == DEFAULTS


@pytest.mark.parametrize("field", ["subject", "issuer", "organization_id"])
def test_feed_settings_require_matching_identity(user_data, field):
    client, current, *_ = user_data
    setattr(current, field, "wrong-owner")
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json=DEFAULTS).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "victim"},
        {"view": "invalid"},
        {"view": None},
        {"email": "other@example.test"},
    ],
)
def test_invalid_preferences_are_rejected_without_overwriting(user_data, payload):
    client = user_data[0]
    assert client.put(PATH, json=payload).status_code == 422
    assert client.get(PATH).json() == DEFAULTS


def test_feed_settings_reset(user_data):
    client = user_data[0]
    client.put(PATH, json={"view": "compact"})
    assert client.put(PATH, json={}).json() == DEFAULTS


@pytest.mark.parametrize("types", [[], ["video"], ["article"] * 7, None])
def test_content_types_reject_invalid_or_empty_selection(user_data, types):
    client = user_data[0]
    assert client.put(PATH, json={"content_types": types}).status_code == 422
    assert client.get(PATH).json() == DEFAULTS


def test_content_types_are_canonical_and_account_scoped(user_data):
    client, current, _, second, _ = user_data
    assert client.put(PATH, json={"content_types": ["tutorial", "article", "tutorial"]}).json() == {
        "view": "cards",
        "content_types": ["article", "tutorial"],
    }
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == DEFAULTS


@pytest.mark.parametrize("interest", ["topic", "source"])
def test_preferences_filter_prepared_feed_and_refresh_on_type_changes(
    user_data, database, interest
):
    from devfeed_core.models import Article, Source, UserRecommendationState
    from devfeed_core.recommendations import expand_recommendation_events, refresh_recommendations
    from sqlalchemy import select, update

    client, _, user, _, topics = user_data
    with database.begin() as session:
        source = session.scalar(select(Source.id).where(Source.approval_status == "approved"))
        ids = list(session.scalars(select(Article.id).order_by(Article.title).limit(60)))
        session.execute(update(Article).where(Article.id.in_(ids)).values(content_type="news"))
    if interest == "topic":
        client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    else:
        client.put(f"/v1/user/preferences/sources/{source}", json={"followed": True})
    refresh_recommendations(database, user)
    old_cursor = client.get("/v1/user/feed?limit=5").json()["next_cursor"]
    # Older accounts have no content_types key; saving the default must keep cursors valid.
    client.put(PATH, json={"view": "compact"})
    assert client.get("/v1/user/feed", params={"cursor": old_cursor}).status_code == 200
    assert client.get("/v1/user/feed").json()["status"] == "ready"
    client.put(PATH, json={"content_types": ["news"]})
    assert client.get("/v1/user/feed").json()["status"] == "refreshing"
    assert refresh_recommendations(database, user) == 60
    assert client.get("/v1/user/feed", params={"cursor": old_cursor}).status_code == 409
    collected = []
    cursor = None
    while True:
        page = client.get(
            "/v1/user/feed", params={"limit": 17, **({"cursor": cursor} if cursor else {})}
        ).json()
        assert all(item["content_type"] == "news" for item in page["items"])
        collected.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(collected) == len(set(collected)) == 60
    # Layout changes preserve this generation, including pagination cursors.
    with database() as session:
        generation = session.get(UserRecommendationState, user).generation
    client.put(PATH, json={"view": "compact", "content_types": ["news"]})
    with database() as session:
        state = session.get(UserRecommendationState, user)
        assert not state.invalidated and state.generation == generation
    with database.begin() as session:
        session.execute(update(Article).where(Article.id.in_(ids)).values(content_type="tutorial"))
    assert client.get("/v1/user/feed").json()["items"] == []
    expand_recommendation_events(database)
    assert refresh_recommendations(database, user) == 0
    client.put(PATH, json={})
    assert refresh_recommendations(database, user) >= 110


def test_public_feed_applies_multiple_types_before_pagination(user_data, database, client):
    from devfeed_core.models import Article
    from sqlalchemy import select, update

    with database.begin() as session:
        ids = list(session.scalars(select(Article.id).order_by(Article.title).limit(70)))
        session.execute(update(Article).where(Article.id.in_(ids[:30])).values(content_type="news"))
        session.execute(
            update(Article).where(Article.id.in_(ids[30:])).values(content_type="tutorial")
        )
    params = [("content_types", "news"), ("content_types", "tutorial"), ("limit", "17")]
    seen = []
    cursor = None
    while True:
        response = client.get("/v1/feed", params=params + ([("cursor", cursor)] if cursor else []))
        assert response.status_code == 200
        page = response.json()
        assert all(item["content_type"] in {"news", "tutorial"} for item in page["items"])
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 70
    explicit = client.get("/v1/feed", params=params + [("content_type", "article")]).json()
    assert explicit["items"] and all(
        item["content_type"] == "article" for item in explicit["items"]
    )
    assert client.get("/v1/feed?content_types=invalid").status_code == 422
