"""Real PostgreSQL ranks, privacy transitions, ties and signed-in ownership."""

import time
from datetime import date

import pytest
from devfeed_core.models import UserAccount, UserReadingStreak
from devfeed_core.user_settings import ProfileSettings
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
    assert [row["username"] for row in streaks] == [""] + [
        f"reader-{index:02}" for index in range(9)
    ]
    assert [row["username"] for row in days] == ["", "reader-11"] + [
        f"reader-{index:02}" for index in range(8)
    ]
    for board in (streaks, days):
        assert len({row["username"] for row in board}) == 10
        assert [row["days"] for row in board] == sorted(
            (row["days"] for row in board), reverse=True
        )
    assert [row["rank"] for row in streaks[:4]] == [1, 2, 2, 4]
    assert streaks[1]["days"] == 50  # Historical best survives an expired current streak.
    assert days[1]["username"] == "reader-11" and days[1]["days"] == 200
    assert set(streaks[0]) == {"username", "display_name", "avatar_url", "days", "rank", "position"}
    text = response.text
    for secret in ["private@example.test", "Private provider name", *map(str, identifiers)]:
        assert secret not in text
    for excluded in ["reader-12", "reader-14"]:
        assert excluded not in text


def test_ties_at_cutoff_keep_ten_distinct_readers_and_global_own_rank(readers, database):
    client, _, identifiers = readers
    with database.begin() as session:
        for account_id in identifiers:
            streak = session.get(UserReadingStreak, account_id)
            if streak:
                streak.longest_days = streak.total_days = 7

    boards = client.get("/v1/user/leaderboard").json()
    for board in boards.values():
        assert [row["username"] for row in board] == [f"reader-{index:02}" for index in range(10)]
        assert [row["rank"] for row in board] == [1] * 10
        assert [row["days"] for row in board] == [7] * 10

    own = client.get("/v1/user/leaderboard/me").json()
    for rank in own.values():
        assert rank["username"] == "reader-10"
        assert rank["rank"] == 1


def test_claiming_username_preserves_existing_rank(readers, database):
    client, current, identifiers = readers
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert all(
        row["username"] == "" and row["rank"] == 1
        for row in client.get("/v1/user/leaderboard/me").json().values()
    )
    with database.begin() as session:
        session.get(UserAccount, identifiers[13]).username = "reader-13"

    boards = client.get("/v1/user/leaderboard").json()
    own = client.get("/v1/user/leaderboard/me").json()
    for metric, board in boards.items():
        assert len(board) == len({row["username"] for row in board}) == 10
        assert board[0]["username"] == "reader-13"
        assert board[0]["rank"] == 1
        assert own[metric] == board[0]
        assert "reader-12" not in {row["username"] for row in board}


def test_own_rank_is_global_outside_top_ten_and_checks_identity(readers):
    client, current, _ = readers
    response = client.get("/v1/user/leaderboard/me")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    own = response.json()
    assert own["longest_streak"]["rank"] == 12
    assert own["reading_days"]["rank"] == 13
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
    assert client.get("/v1/user/leaderboard").json()["longest_streak"][1]["username"] == "reader-00"


def test_empty_and_zero_activity_boards_and_unclaimed_own_rank(readers, database):
    client, current, identifiers = readers
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert all(
        row["username"] == "" for row in client.get("/v1/user/leaderboard/me").json().values()
    )
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


def test_multiple_named_unclaimed_readers_keep_distinct_positions_and_private_accounts_hidden(
    readers, database
):
    client, current, identifiers = readers
    with database.begin() as session:
        for index in (0, 1, 12):
            session.get(UserAccount, identifiers[index]).username = None
        session.get(UserAccount, identifiers[0]).profile = {}
    current.user_id, current.subject = str(identifiers[1]), "reader-1"
    boards = client.get("/v1/user/leaderboard").json()
    own = client.get("/v1/user/leaderboard/me").json()
    for metric, board in boards.items():
        assert [row["position"] for row in board] == list(range(1, 11))
        unclaimed = [row for row in board if row["username"] == ""]
        assert len(unclaimed) == 2
        assert own[metric] in unclaimed
        assert sum(row["position"] == own[metric]["position"] for row in board) == 1
        assert "Public reader 12" not in str(board)
    assert all(row["display_name"] for row in boards["longest_streak"])
    assert client.get("/v1/user/profiles/reader-00").status_code == 404


@pytest.mark.parametrize(
    "profile",
    [
        {},
        {"display_name": None},
        {"display_name": ""},
        {"display_name": " \t\n\r\f\v"},
        {"display_name": "\u00a0\u2003\u3000"},
    ],
)
def test_anonymous_readers_are_excluded_before_ranking_and_top_ten_limit(
    readers, database, profile
):
    client, current, identifiers = readers
    normalized = ProfileSettings.model_validate(profile)
    assert normalized.display_name is None
    with database.begin() as session:
        session.get(UserAccount, identifiers[13]).profile = normalized.model_dump(
            exclude_unset=True
        )

    response = client.get("/v1/user/leaderboard")
    boards = response.json()
    assert [row["username"] for row in boards["longest_streak"]] == [
        f"reader-{index:02}" for index in range(10)
    ]
    assert [row["username"] for row in boards["reading_days"]] == ["reader-11"] + [
        f"reader-{index:02}" for index in range(9)
    ]
    assert [row["rank"] for row in boards["longest_streak"][:3]] == [1, 1, 3]
    for board in boards.values():
        assert [row["position"] for row in board] == list(range(1, 11))
    assert "private@example.test" not in response.text
    assert "Private provider name" not in response.text

    own = client.get("/v1/user/leaderboard/me").json()
    assert own["longest_streak"]["rank"] == 11
    assert own["reading_days"]["rank"] == 12
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }


def test_legacy_space_only_name_does_not_enter_leaderboards(readers, database):
    client, current, identifiers = readers
    with database.begin() as session:
        session.get(UserAccount, identifiers[13]).profile = {"display_name": "   "}

    response = client.get("/v1/user/leaderboard")
    assert response.status_code == 200
    boards = response.json()
    for board in boards.values():
        assert all(row["username"] for row in board)
        assert [row["position"] for row in board] == list(range(1, 11))
    assert [row["rank"] for row in boards["longest_streak"][:3]] == [1, 1, 3]
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }


@pytest.mark.parametrize("identity", ["display_name", "username"])
def test_adding_a_public_name_or_username_enters_existing_reading_activity(
    readers, database, identity
):
    client, current, identifiers = readers
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    with database.begin() as session:
        session.get(UserAccount, identifiers[13]).profile = {}
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }

    with database.begin() as session:
        account = session.get(UserAccount, identifiers[13])
        if identity == "display_name":
            account.profile = ProfileSettings(display_name="  Public name  ").model_dump()
        else:
            account.username = "new-public-name"

    boards = client.get("/v1/user/leaderboard").json()
    own = client.get("/v1/user/leaderboard/me").json()
    for metric, board in boards.items():
        assert len(board) == 10
        assert own[metric] == board[0]
        assert board[0]["rank"] == board[0]["position"] == 1
        assert board[0]["username"] == ("new-public-name" if identity == "username" else "")
        assert board[0]["display_name"] == ("Public name" if identity == "display_name" else None)


def test_claimed_username_keeps_reader_eligible_with_a_blank_display_name(readers, database):
    client, _, identifiers = readers
    with database.begin() as session:
        session.get(UserAccount, identifiers[0]).profile = ProfileSettings(
            display_name=" \t\n\u00a0"
        ).model_dump()
    boards = client.get("/v1/user/leaderboard").json()
    for board in boards.values():
        reader = next(row for row in board if row["username"] == "reader-00")
        assert reader["display_name"] is None


def test_short_boards_only_include_eligible_readers_without_padding(readers, database):
    client, current, identifiers = readers
    with database.begin() as session:
        for index, account_id in enumerate(identifiers):
            account = session.get(UserAccount, account_id)
            streak = session.get(UserReadingStreak, account_id)
            if index == 0:
                account.profile = {}
            elif index == 11:
                streak.current_days = streak.longest_days = streak.total_days = 0
            elif index not in {12, 13, 14}:
                account.username = None
                account.profile = {}
                streak.longest_days = streak.total_days = 10000

    response = client.get("/v1/user/leaderboard")
    assert response.status_code == 200
    boards = response.json()
    for metric, board in boards.items():
        assert len(board) == 2
        assert [row["username"] for row in board] == ["", "reader-00"]
        assert [row["display_name"] for row in board] == ["Public reader 13", None]
        assert [row["rank"] for row in board] == [1, 2]
        assert [row["position"] for row in board] == [1, 2]
        assert [row["days"] for row in board] == (
            [1000, 50] if metric == "longest_streak" else [2000, 60]
        )

    for index, position in ((13, 0), (0, 1)):
        current.user_id, current.subject = str(identifiers[index]), f"reader-{index}"
        own = client.get("/v1/user/leaderboard/me").json()
        assert own == {metric: board[position] for metric, board in boards.items()}

    current.user_id, current.subject = str(identifiers[1]), "reader-1"
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }


@pytest.mark.parametrize("display_name", [None, "", " \t\n\r\f\v", "\u00a0\u2003\u3000"])
def test_clearing_public_name_immediately_removes_unclaimed_reader(readers, display_name):
    client, current, identifiers = readers
    current.user_id, current.subject = str(identifiers[13]), "reader-13"
    assert all(
        row["display_name"] == "Public reader 13"
        for row in client.get("/v1/user/leaderboard/me").json().values()
    )

    saved = client.put("/v1/user/settings/profile", json={"display_name": display_name})
    assert saved.status_code == 200
    assert saved.json()["display_name"] is None

    response = client.get("/v1/user/leaderboard")
    assert response.status_code == 200
    boards = response.json()
    for board in boards.values():
        assert all(row["username"] for row in board)
        assert [row["position"] for row in board] == list(range(1, 11))
    assert [row["rank"] for row in boards["longest_streak"][:3]] == [1, 1, 3]
    assert "Public reader 13" not in response.text
    assert client.get("/v1/user/leaderboard/me").json() == {
        "longest_streak": None,
        "reading_days": None,
    }
