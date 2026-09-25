"""Profile write boundaries and calendar semantics without external services."""

import uuid
from datetime import date

import pytest
from devfeed_core.models import UserReadingStreak, utcnow
from devfeed_core.reading_streaks import reading_streak_value
from devfeed_core.user_settings import ProfileVisibility, UserProfileUpdate, UserStackItem
from pydantic import ValidationError


@pytest.mark.parametrize(
    "username", ["a/b", "has space", "ab", "a" * 31, "admin", "SUPPORT", "üser", "-name"]
)
def test_unsafe_or_reserved_usernames_are_rejected(username):
    with pytest.raises(ValidationError):
        UserProfileUpdate(username=username)


def test_write_contract_normalizes_and_rejects_computed_fields():
    value = UserProfileUpdate(
        username=" Reader-One ",
        bio="x" * 160,
        links=["https://EXAMPLE.com", {"url": "https://github.com/user", "label": " Code "}],
    )
    assert value.username == "reader-one"
    assert value.links[0].url == "https://example.com/"
    assert value.links[1].label == "Code"
    assert UserProfileUpdate(username=" ").username is None
    for data in (
        {"reading_streak": {"current_days": 999}},
        {"bio": "x" * 161},
        {"links": ["javascript:alert(1)"]},
        {"links": ["https://example.com", "https://example.com/"]},
    ):
        with pytest.raises(ValidationError):
            UserProfileUpdate.model_validate(data)


def test_stack_rejects_future_year_and_duplicate_topics():
    topic_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        UserStackItem(topic_id=topic_id, since_year=utcnow().year + 1)
    with pytest.raises(ValidationError):
        UserProfileUpdate(stack=[UserStackItem(topic_id=topic_id)] * 2)


def test_profile_defaults_to_public_with_all_sections_included():
    for stored in ({}, {"location": False, "stack": False, "heatmap": False}):
        visibility = ProfileVisibility.model_validate(stored).model_dump()
        assert visibility == {
            "public": True,
            "location": True,
            "stack": True,
            "heatmap": True,
            "achievements": False,
        }


@pytest.mark.parametrize(
    "today,current", [(date(2024, 2, 29), 3), (date(2024, 3, 1), 3), (date(2024, 3, 2), 0)]
)
def test_streak_expires_without_losing_longest(today, current):
    streak = UserReadingStreak(
        current_days=3, longest_days=5, total_days=8, last_read_date=date(2024, 2, 29)
    )
    assert reading_streak_value(streak, today) == {
        "current_days": current,
        "longest_days": 5,
        "total_days": 8,
        "last_read_date": date(2024, 2, 29),
    }


def test_explicit_private_profile_remains_private():
    assert ProfileVisibility.model_validate({"public": False}).public is False
