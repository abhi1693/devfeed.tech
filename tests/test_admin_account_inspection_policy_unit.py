"""Private account inspection composes saved facts without triggering recommendation work.

Page selection, association scope and aggregate counts are repository boundaries;
these tests validate their HTTP read projection, not SQL filtering or authorization.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_admin_api import users
from devfeed_core.models import (
    PartnerAccount,
    UserAccount,
    UserLink,
    UserReadingStreak,
    UserRecommendationState,
    UserStackAssociation,
)
from taxonomy_command_helpers import topic
from test_admin_editorial_policy_unit import operator_api
from test_admin_user_contracts_unit import account
from test_source_readiness_unit import NOW


@pytest.fixture
def inspection(monkeypatch):
    owner = account(
        about="Maintains distributed services.",
        notification_settings={"sound": True, "show_badge": False},
        feed_settings={"view": "compact", "languages": ["fr"]},
        appearance_settings={"theme": "dark", "timezone": "UTC"},
    )
    owner.profile = {
        **owner.profile,
        "bio": "Backend developer",
        "location": "Bengaluru",
        "visibility": {"public": False},
    }
    topics = [topic("Python"), topic("Rust"), topic("Ruby")]
    stack = [
        (
            UserStackAssociation(
                id=uuid.uuid4(),
                user_id=owner.id,
                topic_id=current.id,
                position=index,
                section="past" if index == 2 else "primary" if index == 0 else "hobby",
                since_year=2020 + index,
            ),
            current,
        )
        for index, current in enumerate(topics)
    ]
    link = UserLink(
        id=uuid.uuid4(),
        user_id=owner.id,
        position=0,
        url="https://developer.example/",
        label="Work",
    )
    partner = PartnerAccount(id=uuid.uuid4(), name="Developer tools", tier="gold", status="active")
    streak = UserReadingStreak(
        user_id=owner.id,
        current_days=2,
        longest_days=4,
        total_days=6,
        last_read_date=NOW.date(),
        updated_at=NOW,
    )
    state = UserRecommendationState(
        user_id=owner.id,
        generation=uuid.uuid4(),
        invalidated=False,
        computed_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
        next_refresh_at=NOW + timedelta(minutes=30),
        attempts=0,
        dispatched_at=None,
        interest_count=4,
        candidates_dirty=False,
        preference_revision=3,
        ranked_at=NOW - timedelta(minutes=5),
    )
    monkeypatch.setattr(users, "utcnow", lambda: NOW)
    read = Mock(return_value=owner)
    monkeypatch.setattr(users, "record", read)
    refresh = Mock()
    monkeypatch.setattr(users, "request_recommendation_refresh", refresh)
    with operator_api(users) as api:
        value = NS(
            api=api,
            owner=owner,
            state=state,
            streak=streak,
            stack=stack,
            topics=topics,
            links=[link],
            partners=[partner],
            counts=(2, 3, 4, 5, 1, 2, 6, 9),
            read=read,
            refresh=refresh,
        )
        yield value


def load(value):
    session = value.api.session
    session.get.side_effect = lambda model, identifier: (
        value.state
        if (model, identifier) == (UserRecommendationState, value.owner.id)
        else value.streak
        if (model, identifier) == (UserReadingStreak, value.owner.id)
        else None
    )
    session.execute.side_effect = [NS(one=lambda: value.counts), NS(all=lambda: value.stack)]
    session.scalars.side_effect = [NS(all=lambda: value.links), NS(all=lambda: value.partners)]
    session.scalar.return_value = NOW
    return value.api.client.get(f"/v1/admin/users/{value.owner.id}")


@pytest.mark.parametrize(
    "status", ["missing", "not-computed", "refreshing", "expired", "legacy-expiry", "ready"]
)
def test_saved_recommendation_state_explains_pending_refresh_or_expiry_without_creating_work(
    inspection, status
):
    v = inspection
    if status == "missing":
        v.state = None
    elif status == "not-computed":
        v.state.generation, v.state.computed_at, v.state.expires_at = None, None, None
        v.state.invalidated = True
    elif status == "refreshing":
        v.state.invalidated, v.state.attempts = True, 1
    elif status == "expired":
        v.state.expires_at = NOW
    elif status == "legacy-expiry":
        v.state.expires_at = None
    response = load(v)
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["feed_status"] == (
        "pending"
        if status in {"missing", "not-computed"}
        else "expired"
        if status in {"expired", "legacy-expiry"}
        else status
    )
    assert output["refresh_attempts"] == (v.state.attempts if v.state else 0)
    assert (
        output["computed_at"] is None
        if status in {"missing", "not-computed"}
        else output["computed_at"] is not None
    )
    v.refresh.assert_not_called()
    v.api.session.add.assert_not_called()
    v.api.session.commit.assert_not_called()


def test_account_detail_keeps_counts_streak_preferences_and_partner_labels_in_their_own_fields(
    inspection,
):
    v = inspection
    response = load(v)
    assert response.status_code == 200, response.text
    output = response.json()
    assert {
        name: output[name]
        for name in (
            "followed_topics",
            "liked_articles",
            "interests",
            "recommendations",
            "followed_sources",
            "bookmarks",
            "reading_days",
            "reads",
        )
    } == dict(
        zip(
            (
                "followed_topics",
                "liked_articles",
                "interests",
                "recommendations",
                "followed_sources",
                "bookmarks",
                "reading_days",
                "reads",
            ),
            v.counts,
            strict=True,
        )
    )
    assert output["reading_streak"] == {
        "current_days": 2,
        "longest_days": 4,
        "total_days": 6,
        "last_read_date": NOW.date().isoformat(),
    }
    assert output["feed_preferences"]["view"] == "compact" and output["feed_preferences"][
        "languages"
    ] == ["fr"]
    assert (
        output["appearance_preferences"]["timezone"] == "UTC"
        and output["notification_preferences"]["sound"]
    )
    assert output["partner_accounts"] == [{"id": str(v.partners[0].id), "name": "Developer tools"}]
    assert output["profile_links"] == [{"label": "Work", "url": "https://developer.example/"}]
    assert output["profile_public"] is False and output["profile_about"] == v.owner.about
    assert output["sign_in_name"] == v.owner.name and output["name"] == "Ada"
    assert (
        "never-expose" not in response.text
        and "private-subject" not in response.text
        and "private-org" not in response.text
    )
    v.read.assert_called_once_with(v.api.session, UserAccount, v.owner.id)


@pytest.mark.parametrize("selection", ["all-current", "only-hobby", "past-only", "empty"])
def test_dev_card_projection_respects_explicit_selection_and_always_excludes_past_stack(
    inspection, selection
):
    v = inspection
    chosen = (
        None
        if selection == "all-current"
        else [v.topics[1].id]
        if selection == "only-hobby"
        else [v.topics[2].id]
        if selection == "past-only"
        else []
    )
    v.owner.profile["dev_card"] = {
        "technologies": [str(identifier) for identifier in chosen] if chosen is not None else None
    }
    response = load(v)
    assert response.status_code == 200, response.text
    output = response.json()
    expected = (
        v.topics[:2]
        if selection == "all-current"
        else [v.topics[1]]
        if selection == "only-hobby"
        else []
    )
    assert output["dev_card_technologies"] == [
        {"id": str(current.id), "name": current.name} for current in expected
    ]
    assert [item["section"] for item in output["stack"]] == ["primary", "hobby", "past"]


def test_account_without_saved_streak_or_enrichment_uses_safe_defaults_and_identity_name(
    inspection,
):
    v = inspection
    v.owner.profile = {}
    v.streak, v.stack, v.links, v.partners = None, [], [], []
    response = load(v)
    assert response.status_code == 200
    output = response.json()
    assert output["name"] == "Sign-in identity"
    assert output["reading_streak"] == {
        "current_days": 0,
        "longest_days": 0,
        "total_days": 0,
        "last_read_date": None,
    }
    assert (
        output["stack"]
        == output["dev_card_technologies"]
        == output["profile_links"]
        == output["partner_accounts"]
        == []
    )


@pytest.mark.parametrize("present", [False, True])
def test_preselected_account_page_preserves_global_total_and_scoped_engagement_counts(
    monkeypatch, present
):
    owner = account(notification_settings={})
    offset = 72 if present else 75
    page = {"items": [owner] if present else [], "total": 73, "limit": 25, "offset": offset}
    paginate = Mock(return_value=page)
    monkeypatch.setattr(users, "paginate", paginate)
    with operator_api(users) as api:
        api.session.execute.return_value = [
            NS(id=owner.id, topics=2, sources=3, likes=4, bookmarks=5, last_read_at=NOW)
        ]
        response = api.client.get("/v1/admin/users", params={"limit": 25, "offset": offset})
        assert response.status_code == 200, response.text
        output = response.json()
        assert (output["total"], output["limit"], output["offset"]) == (73, 25, offset)
        assert len(output["items"]) == int(present)
        if present:
            (item,) = output["items"]
            assert item["followed_topics"] == 2 and item["followed_sources"] == 3
            assert item["liked_articles"] == 4 and item["bookmarks"] == 5
            assert item["last_read_at"] == NOW.isoformat().replace("+00:00", "Z")
            assert "never-expose" not in response.text and "private-subject" not in response.text
        else:
            api.session.execute.assert_not_called()
