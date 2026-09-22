"""UTC activity ledger and rebuildable daily/streak projections."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import (
    UserAccount,
    UserReadingDay,
    UserReadingEvent,
    UserReadingStreak,
    utcnow,
)


def reading_streak_value(streak: UserReadingStreak | None, today: date | None = None) -> dict:
    today = today or utcnow().date()
    active = streak is not None and streak.last_read_date in {today, today - timedelta(days=1)}
    return {
        "current_days": streak.current_days if active and streak else 0,
        "longest_days": streak.longest_days if streak else 0,
        "total_days": streak.total_days if streak else 0,
        "last_read_date": streak.last_read_date if streak else None,
    }


def _lock_user(session: Session, user_id: uuid.UUID) -> None:
    if (
        session.scalar(select(UserAccount.id).where(UserAccount.id == user_id).with_for_update())
        is None
    ):
        raise ValueError("User account unavailable")


def rebuild_reading_history(session: Session, user_id: uuid.UUID) -> None:
    """Repair projections from retained events; caller owns the transaction."""
    _lock_user(session, user_id)
    rows = session.execute(
        select(UserReadingEvent.read_date, func.count(), func.max(UserReadingEvent.occurred_at))
        .where(UserReadingEvent.user_id == user_id)
        .group_by(UserReadingEvent.read_date)
        .order_by(UserReadingEvent.read_date)
    ).all()
    session.execute(delete(UserReadingDay).where(UserReadingDay.user_id == user_id))
    streak = session.get(UserReadingStreak, user_id)
    if streak is None:
        streak = UserReadingStreak(user_id=user_id)
        session.add(streak)
    run = longest = 0
    previous = None
    for day, count, last in rows:
        session.add(
            UserReadingDay(user_id=user_id, read_date=day, article_count=count, last_read_at=last)
        )
        run = run + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    streak.current_days, streak.longest_days, streak.total_days = run, longest, len(rows)
    streak.last_read_date, streak.updated_at = previous, utcnow()
    session.flush()


def record_reading_day(
    session: Session, user_id: uuid.UUID, article_id: uuid.UUID, occurred_at: datetime | None = None
) -> bool:
    """Count a distinct article open once per UTC day; not proof of completion."""
    moment = occurred_at or utcnow()
    if moment.tzinfo is None or moment > utcnow():
        raise ValueError("Reading events need an aware timestamp that is not in the future")
    moment = moment.astimezone(UTC)
    day = moment.date()
    _lock_user(session, user_id)
    recorded = session.scalar(
        insert(UserReadingEvent)
        .values(user_id=user_id, article_id=article_id, read_date=day, occurred_at=moment)
        .on_conflict_do_nothing()
        .returning(UserReadingEvent.article_id)
    )
    if recorded is None:
        return False
    streak = session.get(UserReadingStreak, user_id)
    if streak is None or (streak.last_read_date is not None and day < streak.last_read_date):
        rebuild_reading_history(session, user_id)
        return True
    session.execute(
        insert(UserReadingDay)
        .values(user_id=user_id, read_date=day, article_count=1, last_read_at=moment)
        .on_conflict_do_update(
            index_elements=[UserReadingDay.user_id, UserReadingDay.read_date],
            set_={
                "article_count": UserReadingDay.article_count + 1,
                "last_read_at": func.greatest(UserReadingDay.last_read_at, moment),
            },
        )
    )
    if streak.last_read_date != day:
        streak.current_days = (
            streak.current_days + 1 if streak.last_read_date == day - timedelta(days=1) else 1
        )
        streak.longest_days = max(streak.longest_days, streak.current_days)
        streak.total_days += 1
        streak.last_read_date = day
    streak.updated_at = utcnow()
    session.flush()
    return True
