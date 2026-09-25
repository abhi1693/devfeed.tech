"""Database proof for public boundaries, durable activity and concurrent claims."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import (
    Article,
    Topic,
    UserAccount,
    UserReadingDay,
    UserReadingEvent,
    UserReadingStreak,
)
from devfeed_core.reading_streaks import rebuild_reading_history, record_reading_day
from devfeed_core.topic_deletion import delete_topic
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


def test_public_profile_defaults_public_and_can_be_made_private(user_data, database):
    client, current, first, second, _ = user_data
    path = "/v1/user/settings/profile"
    saved = client.put(
        path,
        json={
            "username": "Reader",
            "location": "Somewhere",
            "bio": "hello",
            "links": [{"url": "https://example.com", "label": "Portfolio"}],
        },
    ).json()
    assert saved["username"] == "reader"
    assert saved["visibility"]["public"] is True
    assert client.get("/v1/user/profiles/reader").status_code == 200
    public = client.get("/v1/user/profiles/READER")
    assert public.headers["cache-control"] == "no-store"
    assert public.json() == {
        "username": "reader",
        "bio": "hello",
        "links": [{"url": "https://example.com/", "label": "Portfolio"}],
        "location": "Somewhere",
        "stack": [],
        "reading_streak": {"current_days": 0, "longest_days": 0, "total_days": 0},
    }
    # Previously stored section switches no longer hide content or block the heatmap.
    with database.begin() as session:
        account = session.get(UserAccount, first)
        account.profile = {
            **account.profile,
            "visibility": {"public": True, "location": False, "stack": False, "heatmap": False},
        }
    assert client.get("/v1/user/profiles/reader").json()["location"] == "Somewhere"
    heatmap = client.get("/v1/user/profiles/reader/reading-heatmap?year=2024").json()
    assert len(heatmap["days"]) == 366 and heatmap["timezone"] == "UTC"
    assert all(day["article_count"] == 0 for day in heatmap["days"])
    assert client.put(path, json={"visibility": {"public": False}}).status_code == 200
    assert client.get("/v1/user/profiles/reader").status_code == 404
    assert client.get("/v1/user/profiles/reader/reading-heatmap?year=2024").status_code == 404
    assert client.get(path).json()["visibility"]["public"] is False
    assert (
        client.put(path, json={"bio": "Updated privately"}).json()["visibility"]["public"] is False
    )
    assert client.get("/v1/user/profiles/reader").status_code == 404
    assert client.put(path, json={"username": "renamed"}).status_code == 409
    assert client.put(path, json={"reading_streak": {"current_days": 999}}).status_code == 422
    current.user_id, current.subject = str(second), "user-b"
    assert client.put(path, json={"username": "READER"}).status_code == 409
    current.user_id, current.subject = str(first), "wrong-owner"
    assert client.get("/v1/user/settings/reading-heatmap?year=2024").status_code == 401


def test_ledger_counts_unique_articles_and_can_rebuild(user_data, database):
    client, _, first, second, _ = user_data
    article = uuid.uuid4()
    moment = datetime(2023, 12, 31, 23, 59, tzinfo=UTC)
    with database.begin() as session:
        assert record_reading_day(session, first, article, moment)
        assert not record_reading_day(session, first, article, moment)
        assert record_reading_day(session, first, article, moment + timedelta(minutes=2))
        record_reading_day(session, first, uuid.uuid4(), moment + timedelta(minutes=2))
        record_reading_day(session, first, article, datetime(2024, 1, 3, tzinfo=UTC))
        # Late day repairs the missing middle of the streak.
        record_reading_day(session, first, article, datetime(2024, 1, 2, tzinfo=UTC))
        streak = session.get(UserReadingStreak, first)
        assert (streak.current_days, streak.longest_days, streak.total_days) == (4, 4, 4)
        session.execute(delete(UserReadingDay).where(UserReadingDay.user_id == first))
        rebuild_reading_history(session, first)
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 5
        assert session.get(UserReadingDay, (first, moment.date())).article_count == 1
        assert session.get(UserReadingStreak, second) is None
    summary = client.get("/v1/user/settings/profile").json()["reading_streak"]
    assert summary["current_days"] == 0 and summary["longest_days"] == 4
    heatmap = client.get("/v1/user/settings/reading-heatmap?year=2024").json()
    assert heatmap["days"][0] == {"date": "2024-01-01", "article_count": 2}
    assert client.get("/v1/user/settings/reading-heatmap?year=0").status_code == 422


def test_concurrent_duplicate_reads_and_username_claims(user_data, database):
    _, _, first, second, _ = user_data
    article = uuid.uuid4()
    moment = datetime(2024, 2, 29, tzinfo=UTC)

    def read(_):
        with database.begin() as session:
            return record_reading_day(session, first, article, moment)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(read, range(8))) == 1

    def claim(user_id):
        try:
            with database.begin() as session:
                session.get(UserAccount, user_id).username = "same-handle"
            return True
        except IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim, [first, second])) == 1
    with database.begin() as session:
        assert session.get(UserReadingDay, (first, moment.date())).article_count == 1
        session.delete(session.get(UserAccount, first))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 0


def test_database_username_checks_and_nullable_accounts(user_data, database):
    _, _, first, second, _ = user_data
    with database() as session:
        assert session.get(UserAccount, first).username is None
        assert session.get(UserAccount, second).username is None
    for name in ["MixedCase", "bad/name", "admin"]:
        with pytest.raises(IntegrityError), database.begin() as session:
            session.get(UserAccount, first).username = name


def test_stack_retirement_and_merge(user_data, database):
    client, _, first, _, ids = user_data
    path = "/v1/user/settings/profile"
    payload = {"stack": [{"topic_id": str(ids[0]), "section": "learning", "since_year": 2020}]}
    assert client.put(path, json=payload).status_code == 200
    assert (
        client.put(
            path, json={"username": "stack-reader", "visibility": {"public": True}}
        ).status_code
        == 200
    )
    assert client.get("/v1/user/profiles/stack-reader").json()["stack"][0]["topic_id"] == str(
        ids[0]
    )
    with database.begin() as session:
        session.get(Topic, ids[0]).status = "rejected"
    assert client.put(path, json=payload).status_code == 200
    assert client.get(path).json()["stack"][0]["status"] == "rejected"
    assert client.get("/v1/user/profiles/stack-reader").json()["stack"] == []
    with database.begin() as session:
        delete_topic(session, ids[0], {"subject": "test"}, replacement_id=ids[1])
    result = client.get(path).json()["stack"]
    assert len(result) == 1 and result[0]["topic_id"] == str(ids[1])
    assert result[0]["section"] == "learning"


def test_reading_history_survives_article_deletion(user_data, database):
    _, _, first, _, _ = user_data
    with database.begin() as session:
        article = session.scalar(select(Article).limit(1))
        record_reading_day(session, first, article.id, datetime(2024, 1, 1, tzinfo=UTC))
        session.execute(delete(Article).where(Article.id == article.id))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserReadingEvent)) == 1
