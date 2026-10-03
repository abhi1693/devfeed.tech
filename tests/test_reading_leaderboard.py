"""Real PostgreSQL ranks, privacy transitions, ties and signed-in ownership."""

import time
from datetime import date

import pytest
from devfeed_core.models import UserAccount, UserReadingStreak
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def readers(database):
    identifiers = []
    with database.begin() as session:
        for index in range(15):
            account = UserAccount(
                issuer="https://identity.example",
                subject=f"reader-{index}",
                organization_id="readers",
                username=None if index == 13 else f"reader-{index:02}",
                name="Private provider name",
                email="private@example.test",
                profile={
                    "display_name": f"Public reader {index}",
                    "avatar_url": None,
                    **({"visibility": {"public": False}} if index == 12 else {}),
                },
            )
            session.add(account)
            session.flush()
            identifiers.append(account.id)
            if index != 14:
                session.add(
                    UserReadingStreak(
                        user_id=account.id,
                        current_days=1,
                        longest_days=50
                        if index in {0, 1}
                        else 1000
                        if index in {12, 13}
                        else 15 - index,
                        total_days=200
                        if index == 11
                        else 2000
                        if index in {12, 13}
                        else 60 - index,
                        last_read_date=date(2024, 1, 1),
                    )
                )

    current = UserIdentity(
        user_id=str(identifiers[10]),
        issuer="https://identity.example",
        subject="reader-10",
        organization_id="readers",
        expires_at=int(time.time()) + 3600,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: current
    with TestClient(app) as client:
        yield client, current, identifiers


def test_public_top_ten_uses_best_streak_and_distinct_days_with_safe_identity(readers):
    client, _, identifiers = readers
    response = client.get("/v1/user/leaderboard")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    boards = response.json()
    streaks, days = boards["longest_streak"], boards["reading_days"]
    assert len(streaks) == len(days) == 10
    assert [row["username"] for row in streaks[:3]] == ["reader-00", "reader-01", "reader-02"]
    assert [row["rank"] for row in streaks[:3]] == [1, 1, 3]
    assert streaks[0]["days"] == 50  # Historical best survives an expired current streak.
    assert days[0]["username"] == "reader-11" and days[0]["days"] == 200
    assert set(streaks[0]) == {"username", "display_name", "avatar_url", "days", "rank"}
    text = response.text
    for secret in ["private@example.test", "Private provider name", *map(str, identifiers)]:
        assert secret not in text
    for excluded in ["reader-12", "reader-14"]:
        assert excluded not in text


def test_own_rank_is_global_outside_top_ten_and_checks_identity(readers):
    client, current, _ = readers
    response = client.get("/v1/user/leaderboard/me")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    own = response.json()
    assert own["longest_streak"]["rank"] == 11
    assert own["reading_days"]["rank"] == 12
    assert own["longest_streak"]["username"] == "reader-10"
    current.subject = "wrong-owner"
    assert client.get("/v1/user/leaderboard/me").status_code == 401


def test_privacy_changes_and_deletion_immediately_remove_readers(readers, database):
    client, current, identifiers = readers
    with database.begin() as session:
        account = session.get(UserAccount, identifiers[0])
        account.profile = {**account.profile, "visibility": {"public": False}}
    current.user_id, current.subject = str(identifiers[0]), "reader-0"
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }
    assert "reader-00" not in client.get("/v1/user/leaderboard").text
    with database.begin() as session:
        session.delete(session.get(UserAccount, identifiers[1]))
    assert "reader-01" not in client.get("/v1/user/leaderboard").text
    with database.begin() as session:
        account = session.get(UserAccount, identifiers[0])
        account.profile = {**account.profile, "visibility": {"public": True}}
    assert client.get("/v1/user/leaderboard").json()["longest_streak"][0]["username"] == "reader-00"


def test_empty_and_zero_activity_boards_and_unclaimed_own_rank(readers, database):
    client, current, identifiers = readers
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }
    with database.begin() as session:
        for account_id in identifiers:
            streak = session.get(UserReadingStreak, account_id)
            if streak:
                streak.current_days = streak.longest_days = streak.total_days = 0
    assert client.get("/v1/user/leaderboard").json() == {"longest_streak": [], "reading_days": []}


def test_public_read_does_not_require_authentication_and_own_rank_does(readers):
    client, _, _ = readers
    client.app.dependency_overrides.pop(require_user)
    assert client.get("/v1/user/leaderboard").status_code == 200
    # An unconfigured identity provider must not turn private ranks into a public read.
    assert client.get("/v1/user/leaderboard/me").status_code in {401, 503}
