"""Profile edits belong to the signed-in account and survive provider refreshes."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import Topic, UserAccount, utcnow
from devfeed_core.reading_streaks import record_reading_day
from devfeed_user_api.accounts import save_user
from sqlalchemy.exc import IntegrityError
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


def test_profile_is_saved_without_changing_sign_in_identity(user_data, database):
    client, current, first, second, _ = user_data
    assert client.get("/v1/user/settings/profile").json() == {
        "display_name": None,
        "avatar_url": None,
        "username": None,
        "bio": None,
        "location": None,
        "about": None,
        "links": [],
        "stack": [],
        "visibility": {
            "public": False,
            "location": True,
            "stack": True,
            "heatmap": True,
            "achievements": False,
        },
        "reading_streak": {
            "current_days": 0,
            "longest_days": 0,
            "total_days": 0,
            "last_read_date": None,
        },
    }
    value = {"display_name": "  Python Fan  ", "avatar_url": "https://example.com/avatar.png"}
    saved = client.put("/v1/user/settings/profile", json=value)
    assert saved.status_code == 200
    assert saved.json()["display_name"] == "Python Fan"
    with database() as session:
        account = session.get(UserAccount, first)
        assert account.name == "User" and account.email == "same@example.test"
        assert session.get(UserAccount, second).profile == {}
    save_user(
        {
            "issuer": current.issuer,
            "subject": current.subject,
            "organization_id": current.organization_id,
            "name": "New provider name",
            "email": "new@example.test",
        }
    )
    assert client.get("/v1/user/settings/profile").json() == saved.json()
    current.user_id, current.subject = str(second), "user-b"
    assert client.get("/v1/user/settings/profile").json() == {
        "display_name": None,
        "avatar_url": None,
        "username": None,
        "bio": None,
        "location": None,
        "about": None,
        "links": [],
        "stack": [],
        "visibility": {
            "public": False,
            "location": True,
            "stack": True,
            "heatmap": True,
            "achievements": False,
        },
        "reading_streak": {
            "current_days": 0,
            "longest_days": 0,
            "total_days": 0,
            "last_read_date": None,
        },
    }


def test_usernames_are_optional_and_unique(user_data, database):
    client, _, _, second, _ = user_data
    with database.begin() as session:
        topic = Topic(name="Python", slug="python", kind="technology", status="active")
        session.add(topic)
        session.flush()
        topic_id = str(topic.id)
    assert (
        client.put("/v1/user/settings/profile", json={"username": "reader"}).json()["username"]
        == "reader"
    )
    assert client.get("/v1/user/settings/profile").json()["username"] == "reader"
    assert client.put("/v1/user/settings/profile", json={"display_name": "Reader"}).json() == {
        "display_name": "Reader",
        "avatar_url": None,
        "username": "reader",
        "bio": None,
        "location": None,
        "about": None,
        "links": [],
        "stack": [],
        "visibility": {
            "public": False,
            "location": True,
            "stack": True,
            "heatmap": True,
            "achievements": False,
        },
        "reading_streak": {
            "current_days": 0,
            "longest_days": 0,
            "total_days": 0,
            "last_read_date": None,
        },
    }
    assert (
        client.put("/v1/user/settings/profile", json={"bio": "Builds useful things."}).json()["bio"]
        == "Builds useful things."
    )
    assert (
        client.put("/v1/user/settings/profile", json={"location": "Bengaluru"}).json()["location"]
        == "Bengaluru"
    )
    assert (
        client.put("/v1/user/settings/profile", json={"display_name": "Reader"}).json()["bio"]
        == "Builds useful things."
    )
    assert client.get("/v1/user/settings/profile").json()["location"] == "Bengaluru"
    stack = client.put(
        "/v1/user/settings/profile",
        json={
            "stack": [
                {"topic_id": topic_id, "section": "primary", "since_year": 2020},
            ]
        },
    )
    assert stack.json()["stack"] == [
        {
            "topic_id": topic_id,
            "section": "primary",
            "since_year": 2020,
            "name": "Python",
            "slug": "python",
            "logo_url": None,
            "status": "active",
        }
    ]
    assert (
        client.put("/v1/user/settings/profile", json={"display_name": "Reader"}).json()["stack"]
        == stack.json()["stack"]
    )
    assert client.put(
        "/v1/user/settings/profile",
        json={"links": ["https://github.com/example", "https://www.linkedin.com/in/example"]},
    ).json()["links"] == [
        {"url": "https://github.com/example", "label": None},
        {"url": "https://www.linkedin.com/in/example", "label": None},
    ]
    assert client.put("/v1/user/settings/profile", json={"display_name": "Reader"}).json()[
        "links"
    ] == [
        {"url": "https://github.com/example", "label": None},
        {"url": "https://www.linkedin.com/in/example", "label": None},
    ]
    assert (
        client.put(
            "/v1/user/settings/profile", json={"about": "I build useful things.\nAnd read a lot."}
        ).json()["about"]
        == "I build useful things.\nAnd read a lot."
    )
    assert (
        client.put("/v1/user/settings/profile", json={"display_name": "Reader"}).json()["about"]
        == "I build useful things.\nAnd read a lot."
    )
    with pytest.raises(IntegrityError), database() as session:
        assert session.get(UserAccount, second).username is None
        session.get(UserAccount, second).username = "reader"
        session.flush()


def test_reading_streak_is_associated_with_authenticated_user(user_data, database):
    client, _, first, _, _ = user_data
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    article_id = uuid.uuid4()
    with database.begin() as session:
        assert record_reading_day(session, first, article_id, yesterday)
        assert not record_reading_day(session, first, article_id, yesterday)
        assert record_reading_day(session, first, article_id, today)
    assert client.get("/v1/user/settings/profile").json()["reading_streak"] == {
        "current_days": 2,
        "longest_days": 2,
        "total_days": 2,
        "last_read_date": today.date().isoformat(),
    }
    heatmap = client.get(f"/v1/user/settings/reading-heatmap?year={today.year}").json()
    assert heatmap["timezone"] == "UTC"
    assert heatmap["metric"] == "distinct_article_opens"
    counts = {item["date"]: item["article_count"] for item in heatmap["days"]}
    assert counts[today.date().isoformat()] == 1
    if yesterday.year == today.year:
        assert counts[yesterday.date().isoformat()] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": "x" * 101},
        {"bio": "x" * 161},
        {"email": "other@example.com"},
        {"user_id": "victim"},
        {"avatar_url": "javascript:alert(1)"},
        {"avatar_url": "http://127.0.0.1/private"},
        {"avatar_url": "https://user:pass@example.com/avatar.png"},
    ],
)
def test_profile_rejects_invalid_or_identity_fields(user_data, payload):
    client = user_data[0]
    assert client.put("/v1/user/settings/profile", json=payload).status_code == 422
    assert client.get("/v1/user/settings/profile").json()["display_name"] is None


def test_profile_checks_identity_binding_for_read_and_write(user_data):
    client, current, *_ = user_data
    current.subject = "wrong-owner"
    assert client.get("/v1/user/settings/profile").status_code == 401
    assert (
        client.put("/v1/user/settings/profile", json={"display_name": "Wrong"}).status_code == 401
    )


def test_reset_to_defaults_normalizes_empty_values(user_data):
    client = user_data[0]
    client.put("/v1/user/settings/profile", json={"display_name": "Override"})
    assert client.put(
        "/v1/user/settings/profile", json={"display_name": " ", "avatar_url": ""}
    ).json() == {
        "display_name": None,
        "avatar_url": None,
        "username": None,
        "bio": None,
        "location": None,
        "about": None,
        "links": [],
        "stack": [],
        "visibility": {
            "public": False,
            "location": True,
            "stack": True,
            "heatmap": True,
            "achievements": False,
        },
        "reading_streak": {
            "current_days": 0,
            "longest_days": 0,
            "total_days": 0,
            "last_read_date": None,
        },
    }
