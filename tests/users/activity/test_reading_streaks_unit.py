"""UTC streak projection policies; database uniqueness is covered by integration tests."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from devfeed_core import reading_streaks as ledger
from devfeed_core.models import UserReadingDay, UserReadingStreak

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
USER, ARTICLE = uuid.UUID(int=1), uuid.UUID(int=2)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(ledger, "utcnow", lambda: NOW)


@pytest.mark.parametrize("days_ago,active", [(0, True), (1, True), (2, False), (-1, False)])
def test_only_today_and_yesterday_keep_a_streak_active(days_ago, active):
    streak = UserReadingStreak(
        current_days=3,
        longest_days=8,
        total_days=15,
        last_read_date=(NOW - timedelta(days=days_ago)).date(),
    )
    value = ledger.reading_streak_value(streak)
    assert value == {
        "current_days": 3 if active else 0,
        "longest_days": 8,
        "total_days": 15,
        "last_read_date": streak.last_read_date,
    }
    assert ledger.reading_streak_value(streak, NOW.date()) == value


def test_absent_streak_has_zero_counts():
    assert ledger.reading_streak_value(None) == {
        "current_days": 0,
        "longest_days": 0,
        "total_days": 0,
        "last_read_date": None,
    }


@pytest.mark.parametrize("moment", [NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_invalid_event_times_are_rejected_before_database_work(moment):
    session = Mock()
    with pytest.raises(ValueError, match="aware timestamp"):
        ledger.record_reading_day(session, USER, ARTICLE, moment)
    session.scalar.assert_not_called()


def test_deleted_user_cannot_create_reading_history():
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(ValueError, match="unavailable"):
        ledger.record_reading_day(session, USER, ARTICLE)
    assert session.scalar.call_count == 1


def test_duplicate_open_does_not_change_streak_or_daily_counts():
    session = Mock()
    session.scalar.side_effect = [USER, None]
    assert not ledger.record_reading_day(session, USER, ARTICLE)
    session.get.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("days_ago,expected", [(0, 4), (1, 5), (3, 1), (None, 1)])
def test_distinct_articles_count_days_once_and_reset_after_a_gap(days_ago, expected):
    streak = UserReadingStreak(
        user_id=USER,
        current_days=4,
        longest_days=4,
        total_days=10,
        last_read_date=(NOW - timedelta(days=days_ago)).date() if days_ago is not None else None,
    )
    session = Mock()
    session.scalar.side_effect = [USER, ARTICLE]
    session.get.return_value = streak
    assert ledger.record_reading_day(session, USER, ARTICLE)
    assert streak.current_days == expected
    assert streak.longest_days == max(4, expected)
    assert streak.total_days == (10 if days_ago == 0 else 11)
    assert streak.last_read_date == NOW.date() and streak.updated_at == NOW
    session.flush.assert_called_once()


@pytest.mark.parametrize("existing", [False, True])
def test_missing_projection_or_out_of_order_event_rebuilds_history(monkeypatch, existing):
    session = Mock()
    session.scalar.side_effect = [USER, ARTICLE]
    session.get.return_value = UserReadingStreak(last_read_date=NOW.date()) if existing else None
    rebuild = Mock()
    monkeypatch.setattr(ledger, "rebuild_reading_history", rebuild)
    moment = datetime(2026, 10, 4, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert ledger.record_reading_day(session, USER, ARTICLE, moment)
    rebuild.assert_called_once_with(session, USER)
    values = session.scalar.call_args.args[0].compile().params
    assert values["read_date"].isoformat() == "2026-10-03"
    assert values["occurred_at"].tzinfo == UTC


@pytest.mark.parametrize(
    "existing,offsets,counts",
    [(False, [], (0, 0, 0)), (False, [0, 1, 2, 5], (1, 3, 4)), (True, [0, 1], (2, 2, 2))],
)
def test_rebuild_uses_retained_events_and_replaces_obsolete_projection(existing, offsets, counts):
    session = Mock()
    session.scalar.return_value = USER
    rows = [((NOW + timedelta(days=i)).date(), i + 2, NOW + timedelta(days=i)) for i in offsets]
    session.execute.return_value.all.return_value = rows
    streak = UserReadingStreak(user_id=USER, current_days=99) if existing else None
    session.get.return_value = streak
    ledger.rebuild_reading_history(session, USER)
    added = [call.args[0] for call in session.add.call_args_list]
    projection = streak or next(value for value in added if isinstance(value, UserReadingStreak))
    assert (projection.current_days, projection.longest_days, projection.total_days) == counts
    assert projection.last_read_date == (rows[-1][0] if rows else None)
    days = [value for value in added if isinstance(value, UserReadingDay)]
    assert [(value.read_date, value.article_count, value.last_read_at) for value in days] == rows
    assert "DELETE FROM user_reading_days" in str(session.execute.call_args.args[0])
    session.flush.assert_called_once()
