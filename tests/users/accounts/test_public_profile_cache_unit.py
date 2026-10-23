"""Public profile caching keeps live visibility checks ahead of reusable content."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.cache import CacheLookup, CacheUnavailable, ResponseCache
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Topic,
    UserAccount,
    UserLink,
    UserReadingStreak,
    UserStackAssociation,
    utcnow,
)
from devfeed_core.user_settings import PublicUserProfile
from devfeed_user_api import profile
from devfeed_user_api.auth import UserIdentity
from fastapi import HTTPException, Response


class ProfileReads:
    """Supply repository read results, without emulating SQL filtering or persistence."""

    def __init__(self, account, *, stack=(), links=(), streak=None):
        self.account = account
        self.stack = stack
        self.links = links
        self.streak = streak
        self.events = []
        self.after_expire = None
        self.read_error = None

    def scalar(self, _):
        self.events.append("visibility")
        return self.account

    def execute(self, _):
        self.events.append("stack")
        if self.read_error:
            raise self.read_error
        return SimpleNamespace(all=lambda: list(self.stack))

    def scalars(self, _):
        self.events.append("links")
        return list(self.links)

    def get(self, model, identifier):
        assert model is UserReadingStreak and identifier == self.account.id
        self.events.append("streak")
        return self.streak

    def expire(self, account):
        assert account is self.account
        self.events.append("expire")
        if self.after_expire:
            self.after_expire()


@pytest.fixture
def reads():
    account = UserAccount(
        id=uuid.uuid4(),
        issuer="https://identity.example",
        subject="private-provider-subject",
        organization_id="private-organization",
        username="reader-one",
        name="Private provider name",
        email="private-reader@example.com",
        about="A saved public introduction.",
        profile={
            "display_name": "Saved Reader",
            "avatar_url": "https://images.example/avatar.png",
            "avatar_variants": [{"width": 64, "url": "https://images.example/avatar-64.webp"}],
            "bio": "Builds useful software.",
            "location": "Bengaluru",
            "experience_level": "senior",
            "visibility": {"public": True, "location": False, "stack": False, "heatmap": False},
            "dev_card": {"theme": "terminal", "motion": "static"},
        },
    )
    active = Topic(
        id=uuid.uuid4(),
        name="Python",
        slug="python",
        kind="language",
        status="active",
        logo_url=None,
        managed_logo={},
    )
    inactive = Topic(
        id=uuid.uuid4(),
        name="Old framework",
        slug="old-framework",
        kind="framework",
        status="rejected",
        logo_url=None,
        managed_logo={},
    )
    stack = [
        (
            UserStackAssociation(
                user_id=account.id,
                topic_id=topic.id,
                section="primary",
                since_year=2020,
                position=position,
            ),
            topic,
        )
        for position, topic in enumerate([active, inactive])
    ]
    links = [
        UserLink(
            user_id=account.id, position=0, url="https://github.com/reader-one", label="GitHub"
        )
    ]
    streak = UserReadingStreak(
        user_id=account.id,
        current_days=3,
        longest_days=8,
        total_days=12,
        last_read_date=utcnow().date(),
    )
    return ProfileReads(account, stack=stack, links=links, streak=streak)


@pytest.fixture
def cache_state(monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    lookup = CacheLookup(
        generation_key="test:public:generation",
        generation=b"a" * 32,
        key="test:public:profile",
        lock_key="test:public:profile:lock",
        token="loader-token",
    )
    cache = Mock(spec=ResponseCache)
    cache.lookup.return_value = lookup
    cache.publish.return_value = True
    cache.release.return_value = None
    provider = Mock(return_value=cache)
    observations = Mock()
    monkeypatch.setattr(profile, "get_cache", provider)
    monkeypatch.setattr(profile, "record_cache_read", observations)
    return SimpleNamespace(cache=cache, lookup=lookup, provider=provider, observations=observations)


def test_public_projection_excludes_identity_and_experience_and_omits_inactive_stack(reads):
    value = profile.public_profile_value(reads, reads.account)
    payload = value.model_dump(mode="json")

    assert isinstance(value, PublicUserProfile)
    assert payload["username"] == "reader-one" and payload["display_name"] == "Saved Reader"
    assert payload["about"] == reads.account.about
    assert payload["location"] == "Bengaluru"
    assert payload["avatar_variants"][0]["width"] == 64
    assert payload["links"] == [{"url": "https://github.com/reader-one", "label": "GitHub"}]
    assert [item["slug"] for item in payload["stack"]] == ["python"]
    assert payload["reading_streak"]["current_days"] == 3
    assert payload["dev_card"]["theme"] == "terminal"
    assert (
        not {
            "id",
            "email",
            "issuer",
            "subject",
            "organization_id",
            "experience_level",
            "visibility",
            "followed_topics",
        }
        & payload.keys()
    )
    assert "private-reader@example.com" not in value.model_dump_json()
    assert "private-provider-subject" not in value.model_dump_json()


@pytest.mark.parametrize("state", ["private", "deleted"])
def test_unavailable_profile_is_checked_before_even_a_valid_cache_hit(reads, cache_state, state):
    cache_state.cache.lookup.return_value = CacheLookup(
        **{
            **vars(cache_state.lookup),
            "token": None,
            "body": PublicUserProfile(username="reader-one", display_name="Previously public")
            .model_dump_json()
            .encode(),
        }
    )
    if state == "private":
        reads.account.profile = {**reads.account.profile, "visibility": {"public": False}}
    else:
        reads.account = None

    with pytest.raises(HTTPException) as raised:
        profile.public_profile("reader-one", reads, Response())

    assert (raised.value.status_code, raised.value.detail) == (404, "Profile not found")
    cache_state.provider.assert_not_called()
    assert reads.events == ["visibility"]


@pytest.mark.parametrize("username", ["SUPPORT", "ab", "has space"])
def test_invalid_public_username_returns_not_found_without_cache_or_account_reads(
    reads, cache_state, username
):
    with pytest.raises(HTTPException) as raised:
        profile.public_profile(username, reads, Response())

    assert (raised.value.status_code, raised.value.detail) == (404, "Profile not found")
    assert reads.events == []
    cache_state.provider.assert_not_called()


def test_disabled_cache_serves_the_public_projection_with_bypass_headers(
    reads, cache_state, monkeypatch
):
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    response = Response()

    value = profile.public_profile("READER-ONE", reads, response)

    assert value.display_name == "Saved Reader"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Cache"] == "BYPASS"
    cache_state.provider.assert_not_called()
    cache_state.observations.assert_called_once_with("public_profile", "bypass", "disabled")


def test_unavailable_cache_falls_back_without_creating_a_cache_lease(reads, cache_state):
    cache_state.cache.lookup.side_effect = CacheUnavailable
    response = Response()

    value = profile.public_profile("reader-one", reads, response)

    assert value.username == "reader-one" and len(value.stack) == 1
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Cache"] == "BYPASS"
    cache_state.cache.publish.assert_not_called()
    cache_state.cache.release.assert_not_called()
    cache_state.observations.assert_called_once_with(
        "public_profile", "bypass", "cache_unavailable"
    )


def test_cache_hit_keeps_live_visibility_check_and_avoids_loading_public_projection(
    reads, cache_state
):
    cached = PublicUserProfile(username="reader-one", display_name="Cached Reader")
    lookup = CacheLookup(
        **{**vars(cache_state.lookup), "token": None, "body": cached.model_dump_json().encode()}
    )
    cache_state.cache.lookup.return_value = lookup
    response = Response()

    value = profile.public_profile("READER-ONE", reads, response)

    assert value == cached
    assert reads.events == ["visibility"]
    assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Cache"] == "HIT"
    cache_state.cache.publish.assert_not_called()
    cache_state.cache.release.assert_called_once_with(lookup)
    cache_state.observations.assert_called_once_with("public_profile", "hit")


@pytest.mark.parametrize(
    "body",
    [
        b"null",
        b'"unexpected cached string"',
        b'{"display_name":"Missing username"}',
        b'{"username":"reader-one","email":"private@example.com"}',
    ],
)
def test_schema_invalid_cached_json_falls_back_without_exposing_cached_fields(
    reads, cache_state, body
):
    # All bodies are valid JSON, so they can pass the shared cache's JSON check.
    lookup = replace(cache_state.lookup, body=body, token=None)
    cache_state.cache.lookup.return_value = lookup
    cache_state.cache.publish.return_value = False  # A hit owns no loader lock.
    response = Response()

    value = profile.public_profile("reader-one", reads, response)

    assert value.display_name == "Saved Reader"
    assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Cache"] == "MISS"
    assert reads.events[:3] == ["visibility", "expire", "visibility"]
    cached_lookup, encoded, ttl = cache_state.cache.publish.call_args.args
    assert cached_lookup == lookup and PublicUserProfile.model_validate_json(encoded) == value
    assert ttl == get_settings().cache_public_profile_ttl_seconds
    assert b"private@example.com" not in encoded and b"experience_level" not in encoded
    cache_state.cache.release.assert_called_once_with(lookup)
    cache_state.observations.assert_called_once_with("public_profile", "miss")


def test_cache_miss_expires_the_identity_map_before_publishing_updated_fields(reads, cache_state):
    def refresh_account():
        reads.account.profile = {**reads.account.profile, "display_name": "Updated Reader"}

    reads.after_expire = refresh_account
    response = Response()

    value = profile.public_profile("reader-one", reads, response)

    assert value.display_name == "Updated Reader"
    assert (
        PublicUserProfile.model_validate_json(
            cache_state.cache.publish.call_args.args[1]
        ).display_name
        == "Updated Reader"
    )
    assert response.headers["X-Cache"] == "MISS"
    cache_state.cache.release.assert_called_once_with(cache_state.lookup)


@pytest.mark.parametrize("change", ["private", "deleted"])
def test_generation_reload_cannot_publish_an_account_hidden_after_initial_visibility_read(
    reads, cache_state, change
):
    def refresh_account():
        if change == "private":
            reads.account.profile = {**reads.account.profile, "visibility": {"public": False}}
        else:
            reads.account = None

    reads.after_expire = refresh_account

    with pytest.raises(HTTPException) as raised:
        profile.public_profile("reader-one", reads, Response())

    assert (raised.value.status_code, raised.value.detail) == (404, "Profile not found")
    assert reads.events == ["visibility", "expire", "visibility"]
    cache_state.cache.publish.assert_not_called()
    cache_state.cache.release.assert_called_once_with(cache_state.lookup)


def test_projection_read_failure_releases_its_cache_lock_without_publishing_partial_data(
    reads, cache_state
):
    reads.read_error = RuntimeError("Repository read failed")

    with pytest.raises(RuntimeError, match="Repository read failed"):
        profile.public_profile("reader-one", reads, Response())

    cache_state.cache.publish.assert_not_called()
    cache_state.cache.release.assert_called_once_with(cache_state.lookup)


@pytest.mark.parametrize("failure", ["publish", "release", "lost-generation"])
def test_cache_write_or_release_failure_does_not_discard_a_fresh_public_profile(
    reads, cache_state, failure
):
    if failure == "lost-generation":
        cache_state.cache.publish.return_value = False
    else:
        getattr(cache_state.cache, failure).side_effect = CacheUnavailable
    response = Response()

    value = profile.public_profile("reader-one", reads, response)

    assert value.display_name == "Saved Reader"
    assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Cache"] == "MISS"
    cache_state.cache.release.assert_called_once_with(cache_state.lookup)
    cache_state.observations.assert_called_once_with("public_profile", "miss")


class HistoryReads:
    """Supply an account and its already scoped daily reading aggregates."""

    def __init__(self, account, counts):
        self.account = account
        self.counts = counts
        self.events = []

    def scalar(self, _):
        self.events.append("account")
        return self.account

    def execute(self, _):
        self.events.append("history")
        return SimpleNamespace(all=lambda: list(self.counts.items()))


def owner(account):
    return UserIdentity(
        user_id=str(account.id),
        issuer=account.issuer,
        subject=account.subject,
        organization_id=account.organization_id,
        expires_at=4_102_444_800,
        csrf_token="unit-profile-csrf",
    )


@pytest.mark.parametrize(
    "requested,year,days",
    [(2000, 2000, 366), (2024, 2024, 366), (2025, 2025, 365), (None, 2026, 365)],
)
def test_heatmap_zero_fills_the_selected_utc_calendar_without_losing_recorded_counts(
    reads, monkeypatch, requested, year, days
):
    monkeypatch.setattr(profile, "utcnow", lambda: datetime(2026, 10, 22, 0, 1, tzinfo=UTC))
    counts = {date(year, 1, 1): 2, date(year, 8, 2): 4}
    history = HistoryReads(reads.account, counts)

    result = profile.heatmap_value(history, reads.account.id, requested)

    assert result.year == year and result.timezone == "UTC"
    assert result.metric == "distinct_article_opens"
    assert len(result.days) == days
    assert result.days[0].date == date(year, 1, 1)
    assert result.days[-1].date == date(year, 12, 31)
    assert sum(day.article_count for day in result.days) == 6
    assert {day.date: day.article_count for day in result.days if day.article_count} == counts
    assert (
        (date(year, 2, 29) in {day.date for day in result.days})
        if days == 366
        else all(not (day.date.month == 2 and day.date.day == 29) for day in result.days)
    )


@pytest.mark.parametrize("year", [1999, 2027])
def test_heatmap_rejects_unsupported_years_before_loading_reading_history(reads, monkeypatch, year):
    monkeypatch.setattr(profile, "utcnow", lambda: datetime(2026, 10, 22, tzinfo=UTC))
    history = HistoryReads(reads.account, {})

    with pytest.raises(HTTPException) as raised:
        profile.heatmap_value(history, reads.account.id, year)

    assert raised.value.status_code == 422
    assert history.events == []


def test_public_heatmap_checks_visibility_and_retains_no_store_without_using_the_card_cache(
    reads, cache_state
):
    history = HistoryReads(reads.account, {date(2024, 2, 29): 3})
    response = Response()

    result = profile.public_heatmap("READER-ONE", history, response, year=2024)

    assert result.year == 2024 and sum(day.article_count for day in result.days) == 3
    assert history.events == ["account", "history"]
    assert response.headers["Cache-Control"] == "no-store"
    cache_state.provider.assert_not_called()


@pytest.mark.parametrize("state", ["private", "deleted"])
def test_hidden_public_heatmaps_cannot_load_or_expose_reading_activity(reads, state):
    if state == "private":
        reads.account.profile = {**reads.account.profile, "visibility": {"public": False}}
    history = HistoryReads(reads.account if state == "private" else None, {})

    with pytest.raises(HTTPException) as raised:
        profile.public_heatmap("reader-one", history, Response(), year=2024)

    assert (raised.value.status_code, raised.value.detail) == (404, "Profile not found")
    assert history.events == ["account"]


def test_profile_privacy_does_not_prevent_the_owner_from_reading_saved_settings_or_history(reads):
    reads.account.profile = {**reads.account.profile, "visibility": {"public": False}}
    user = owner(reads.account)
    history = HistoryReads(reads.account, {date(2024, 2, 29): 3})

    settings = profile.profile(user, reads)
    calendar = profile.reading_heatmap(user, history, year=2024)

    assert settings.visibility.public is False
    assert settings.experience_level == "senior"
    assert [item.status for item in settings.stack] == ["active", "rejected"]
    assert sum(day.article_count for day in calendar.days) == 3
    assert history.events == ["account", "history"]


@pytest.mark.parametrize("endpoint", ["profile", "reading_heatmap"])
def test_missing_owned_account_rejects_profile_or_history_before_projecting_data(reads, endpoint):
    user = owner(reads.account)
    history = HistoryReads(None, {})

    with pytest.raises(HTTPException) as raised:
        getattr(profile, endpoint)(user, history)

    assert (raised.value.status_code, raised.value.detail) == (401, "User account unavailable")
    assert history.events == ["account"]
