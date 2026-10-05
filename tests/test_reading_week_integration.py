"""Bounded reading-week results use the real ledger and authenticated account."""

from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import UserAccount, UserReadingDay
from devfeed_user_api import profile
from sqlalchemy import event
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration

NOW = datetime(2027, 1, 2, 0, 1, tzinfo=UTC)
PATH = "/v1/user/settings/reading-week"


def test_week_is_bounded_and_scoped_without_public_username(user_data, database, monkeypatch):
    client, identity, first, second, _ = user_data
    monkeypatch.setattr(profile, "utcnow", lambda: NOW)
    with database.begin() as session:
        assert session.get(UserAccount, first).username is None
        session.add_all(
            UserReadingDay(
                user_id=user_id,
                read_date=(NOW + timedelta(days=offset)).date(),
                article_count=count,
                last_read_at=NOW + timedelta(days=offset),
            )
            for user_id, offset, count in (
                (first, -7, 99),
                (first, -6, 2),
                (first, -2, 5),
                (first, 0, 3),
                (first, 1, 99),
                (second, -6, 30),
                (second, 0, 40),
            )
        )
    queries = []

    def collect_queries(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM user_reading_days" in statement:
            queries.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", collect_queries)
    try:
        response = client.get(f"{PATH}?user_id={second}&days=10000")
    finally:
        event.remove(engine, "before_cursor_execute", collect_queries)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    week = response.json()
    assert week["today"] == "2027-01-02" and week["timezone"] == "UTC"
    assert [day["date"] for day in week["days"]] == [
        (NOW.date() - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)
    ]
    assert [day["article_count"] for day in week["days"]] == [2, 0, 0, 0, 5, 0, 3]
    assert len(queries) == 1
    assert "user_reading_days.user_id =" in queries[0]
    assert "user_reading_days.read_date >=" in queries[0]
    assert "user_reading_days.read_date <=" in queries[0]

    identity.user_id, identity.subject = str(second), "user-b"
    assert [day["article_count"] for day in client.get(PATH).json()["days"]] == [
        30,
        0,
        0,
        0,
        0,
        0,
        40,
    ]


@pytest.mark.parametrize("field", ["user_id", "issuer", "subject", "organization_id"])
def test_forged_identity_cannot_read_another_account(user_data, monkeypatch, field):
    client, identity, _, second, _ = user_data
    monkeypatch.setattr(profile, "utcnow", lambda: NOW)
    setattr(identity, field, str(second) if field == "user_id" else "wrong-identity")

    assert client.get(PATH).status_code == 401


def test_account_without_history_gets_seven_empty_days(user_data, monkeypatch):
    client, _, _, _, _ = user_data
    monkeypatch.setattr(profile, "utcnow", lambda: NOW)

    response = client.get(PATH)

    assert response.status_code == 200
    assert len(response.json()["days"]) == 7
    assert all(day["article_count"] == 0 for day in response.json()["days"])
