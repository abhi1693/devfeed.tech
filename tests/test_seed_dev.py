import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("seed_dev", ROOT / "scripts/seed_dev.py")
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


@pytest.fixture
def sample():
    return json.loads((ROOT / "dev/seed/published.json").read_text())


def test_published_snapshot_has_complete_references_and_all_reader_types(sample):
    assert seed.validate_snapshot(sample) is sample
    assert {a["content_type"] for a in sample["articles"]} == {
        "article",
        "news",
        "tutorial",
        "release",
        "comparison",
        "opinion",
    }
    assert len(sample["articles"]) > 48  # Exercise more than two reader pages.
    assert all(a["canonical_url"].startswith("https://") for a in sample["articles"])


@pytest.mark.parametrize("mutation", ["duplicate", "topic", "publication", "date", "sources"])
def test_invalid_snapshot_is_rejected_before_writes(sample, mutation):
    value = deepcopy(sample)
    if mutation == "duplicate":
        value["articles"].append(value["articles"][0])
    elif mutation == "topic":
        value["topics"] = []
    elif mutation == "publication":
        value["articles"][0]["published_to_feed_at"] = None
    elif mutation == "sources":
        value["articles"][0]["sources"] = []
    else:
        value["articles"][0]["published_at"] = "2026-09-01T00:00:00"
    with pytest.raises(ValueError):
        seed.validate_snapshot(value)


def test_local_target_must_match_the_inspected_compose_server():
    url = make_url("postgresql+psycopg://devfeed@postgres/devfeed")
    seed.validate_target(url, "devfeed", "172.18.0.2", ["172.18.0.2"])
    for changed_url, database, address in [
        (url.set(host="production.example"), "devfeed", "172.18.0.2"),
        (url.set(database="production"), "production", "172.18.0.2"),
        (url, "devfeed", "172.18.0.99"),
        (url, "other", "172.18.0.2"),
    ]:
        with pytest.raises(ValueError, match="local Compose"):
            seed.validate_target(changed_url, database, address, ["172.18.0.2"])


@pytest.mark.integration
def test_seed_readers_have_consistent_activity_and_populate_both_boards(database):
    from datetime import timedelta

    from devfeed_core.models import UserAccount, UserReadingDay, UserReadingStreak
    from devfeed_user_api.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    with database.begin() as session:
        counts = seed.import_readers(session)
        assert counts["users"] == 12 and counts["skipped_users"] == 0
        accounts = session.scalars(select(UserAccount).order_by(UserAccount.username)).all()
        assert len(accounts) == 12
        assert all(
            account.email is None and account.issuer == "https://seed.invalid"
            for account in accounts
        )
        total = 0
        for account in accounts:
            streak = session.get(UserReadingStreak, account.id)
            dates = session.scalars(
                select(UserReadingDay.read_date)
                .where(UserReadingDay.user_id == account.id)
                .order_by(UserReadingDay.read_date)
            ).all()
            longest = run = 0
            previous = None
            for day in dates:
                run = run + 1 if previous and day == previous + timedelta(days=1) else 1
                longest = max(longest, run)
                previous = day
            assert len(dates) == streak.total_days
            assert longest == streak.longest_days and run == streak.current_days
            assert dates[-1] == streak.last_read_date
            total += len(dates)
        assert total == counts["reading_days"]
    with TestClient(create_app()) as client:
        boards = client.get("/v1/user/leaderboard").json()
    assert len(boards["longest_streak"]) == len(boards["reading_days"]) == 10
    assert [row["rank"] for row in boards["longest_streak"][:3]] == [1, 1, 3]
    assert boards["longest_streak"][0]["username"] != boards["reading_days"][0]["username"]
    with database.begin() as session:
        account = session.scalar(select(UserAccount).where(UserAccount.username == "seed-maya"))
        account.profile = {"display_name": "Local edit", "visibility": {"public": False}}
        account_id = account.id
    with database.begin() as session:
        assert seed.import_readers(session) == {"users": 0, "skipped_users": 12, "reading_days": 0}
        assert session.get(UserAccount, account_id).profile["display_name"] == "Local edit"
        assert session.get(UserAccount, account_id).profile["visibility"]["public"] is False
