"""A small private UTC calendar cannot reveal another account's reading history."""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest
from devfeed_core.user_settings import UserReadingWeek
from devfeed_user_api import auth, profile
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.dependencies import get_session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

USER = uuid.UUID(int=1)
NOW = datetime(2027, 1, 2, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize("today", [date(2027, 1, 2), date(2024, 2, 29), date(2024, 3, 1)])
def test_week_contains_seven_zero_filled_dates_across_calendar_boundaries(monkeypatch, today):
    monkeypatch.setattr(profile, "utcnow", lambda: datetime.combine(today, NOW.timetz()))
    session = Mock()
    session.execute.return_value.all.return_value = []

    value = profile.reading_week_value(session, USER)

    assert value.today == today and value.timezone == "UTC"
    assert [(day.date, day.article_count) for day in value.days] == [
        (today - timedelta(days=offset), 0) for offset in range(6, -1, -1)
    ]
    session.execute.assert_called_once()
    params = session.execute.call_args.args[0].compile().params
    assert params == {
        "user_id_1": USER,
        "read_date_1": today - timedelta(days=6),
        "read_date_2": today,
    }


def test_week_preserves_counts_without_adding_older_or_future_days(monkeypatch):
    monkeypatch.setattr(profile, "utcnow", lambda: NOW)
    today = NOW.date()
    session = Mock()
    session.execute.return_value.all.return_value = [
        (today - timedelta(days=7), 99),
        (today - timedelta(days=6), 2),
        (today - timedelta(days=2), 5),
        (today, 3),
        (today + timedelta(days=1), 99),
    ]

    value = profile.reading_week_value(session, USER)

    assert [day.article_count for day in value.days] == [2, 0, 0, 0, 5, 0, 3]
    assert value.days[0].date == date(2026, 12, 27)
    assert value.days[-1].date == today


@pytest.mark.parametrize("count", [0, 6, 8])
def test_week_response_contract_requires_exactly_seven_days(count):
    with pytest.raises(ValidationError):
        UserReadingWeek(
            today=NOW.date(),
            days=[{"date": NOW.date(), "article_count": 0}] * count,
        )


@pytest.fixture
def week_route(monkeypatch):
    monkeypatch.setattr(profile, "utcnow", lambda: NOW)
    session = Mock()
    session.scalar.return_value = Mock(id=USER)
    session.execute.return_value.all.return_value = [(NOW.date(), 4)]
    user = UserIdentity(
        user_id=str(USER),
        issuer="https://identity.example",
        subject="reader",
        organization_id="org-1",
        expires_at=2_000_000_000,
        csrf_token="test",
    )
    app = FastAPI()
    app.include_router(profile.router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client, app, session, user


def test_week_route_is_private_and_ignores_requested_account_or_date(week_route):
    client, _, session, user = week_route

    response = client.get(
        "/v1/user/settings/reading-week?user_id=2&year=1999&days=10000&today=2099-01-01"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "today": "2027-01-02",
        "timezone": "UTC",
        "days": [
            {"date": (NOW.date() - timedelta(days=offset)).isoformat(), "article_count": 0}
            for offset in range(6, 0, -1)
        ]
        + [{"date": "2027-01-02", "article_count": 4}],
    }
    assert session.scalar.call_args.args[0].compile().params == {
        "id_1": USER,
        "issuer_1": user.issuer,
        "subject_1": user.subject,
        "organization_id_1": user.organization_id,
    }


def test_missing_owned_account_does_not_query_reading_history(week_route):
    client, _, session, _ = week_route
    session.scalar.return_value = None

    assert client.get("/v1/user/settings/reading-week").status_code == 401
    session.execute.assert_not_called()


def test_anonymous_request_is_rejected_before_querying_history(week_route, monkeypatch):
    client, app, session, _ = week_route
    app.dependency_overrides.pop(require_user)
    monkeypatch.setattr(auth, "require_config", lambda: None)

    assert client.get("/v1/user/settings/reading-week").status_code == 401
    session.scalar.assert_not_called()
    session.execute.assert_not_called()
