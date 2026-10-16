"""Acquisition exports reconcile retained first-party outcomes and GA delivery gaps."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from devfeed_cli import reader_commands
from devfeed_cli.main import app
from devfeed_core.models import (
    ReaderMilestoneDelivery,
    ReaderSignup,
    UserAccount,
    UserReadingEvent,
)
from devfeed_core.reader_acquisition import record_verified_signup
from devfeed_core.reader_acquisition_report import reader_acquisition_report
from sqlalchemy import select, text
from typer.testing import CliRunner

OBSERVED = datetime(2026, 10, 17, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "start,end,observed",
    [
        (date(2026, 10, 17), date(2026, 10, 17), OBSERVED),
        (date(2026, 10, 17), date(2026, 10, 16), OBSERVED),
        (date(2026, 1, 1), date(2026, 10, 17), OBSERVED),
        (date(2026, 10, 18), date(2026, 10, 19), OBSERVED),
        (date(2026, 10, 17), date(2026, 10, 19), OBSERVED),
        (date(2026, 10, 16), date(2026, 10, 17), OBSERVED.replace(tzinfo=None)),
    ],
)
def test_report_rejects_unbounded_future_or_ambiguous_windows_without_reading_database(
    start, end, observed
):
    with pytest.raises(ValueError):
        reader_acquisition_report(None, start=start, end=end, observed_at=observed)


def test_report_cli_rejects_bad_dates_before_database_access(monkeypatch):
    monkeypatch.setattr(reader_commands, "session_factory", lambda: pytest.fail("Opened DB"))
    result = CliRunner().invoke(
        app, ["readers", "acquisition-report", "--start", "not-a-date", "--end", "2026-10-18"]
    )
    assert result.exit_code == 2
    assert "Invalid command input" in result.output


def account(session, signed_up_at):
    user = UserAccount(
        issuer="https://identity.example",
        subject=str(uuid.uuid4()),
        organization_id="readers",
        name="Private name",
        email="private@example.test",
        created_at=signed_up_at or OBSERVED - timedelta(days=10),
        reader_signup_at=signed_up_at,
    )
    session.add(user)
    session.flush()
    return user


def context(status, capture, *, measurable=True):
    return {
        "version": 1,
        "status": status,
        "source": "google" if status == "attributed" else "direct" if status == "direct" else None,
        "medium": "organic"
        if status == "attributed"
        else "none"
        if status == "direct"
        else "unknown",
        "campaign": None,
        "landing_route": "article",
        "captured_at": int(capture.timestamp()),
        "client_platform": "web",
        "measurement": "eligible" if measurable else "unavailable",
        **({"client_id": "123.456", "session_id": 123} if measurable else {}),
    }


def reading(session, user, moment):
    session.add(
        UserReadingEvent(
            user_id=user.id,
            article_id=uuid.uuid4(),
            read_date=moment.astimezone(UTC).date(),
            occurred_at=moment,
        )
    )


@pytest.mark.integration
def test_report_reconciles_unknown_attribution_maturity_and_provider_uncertainty(database):
    with database.begin() as session:
        organic = account(session, OBSERVED - timedelta(days=2))
        record_verified_signup(
            session,
            organic.id,
            organic.reader_signup_at,
            context("attributed", OBSERVED - timedelta(days=3)),
        )
        organic_signup = session.get(ReaderSignup, organic.id)
        organic_signup.activated_at = organic.reader_signup_at + timedelta(hours=1)
        organic_signup.activation_article_id = uuid.uuid4()
        session.add(
            UserReadingEvent(
                user_id=organic.id,
                article_id=organic_signup.activation_article_id,
                read_date=organic_signup.activated_at.date(),
                occurred_at=organic_signup.activated_at,
            )
        )
        delivery = session.get(ReaderMilestoneDelivery, (organic.id, "sign_up"))
        delivery.status, delivery.attempts = "http_accepted", 1
        session.add(
            ReaderMilestoneDelivery(
                user_id=organic.id,
                event_name="reader_activation",
                occurred_at=organic_signup.activated_at,
                status="unknown",
                reason="transport_unresolved",
                attempts=1,
            )
        )
        direct = account(session, OBSERVED - timedelta(days=8))
        record_verified_signup(
            session,
            direct.id,
            direct.reader_signup_at,
            context("direct", OBSERVED - timedelta(days=9), measurable=False),
        )
        reading(session, direct, direct.reader_signup_at - timedelta(seconds=1))
        reading(session, direct, direct.reader_signup_at + timedelta(days=7))
        unknown = account(session, OBSERVED - timedelta(days=1))
        record_verified_signup(
            session,
            unknown.id,
            unknown.reader_signup_at,
            context("unknown", OBSERVED - timedelta(days=1)),
        )
        reading(session, unknown, OBSERVED + timedelta(seconds=1))
        gap = account(session, OBSERVED - timedelta(days=1))
        reading(session, gap, gap.reader_signup_at + timedelta(hours=1))
        # Other identity materialization and older accounts never become signup
        # outcomes just because a reading event exists.
        generic = account(session, None)
        reading(session, generic, OBSERVED - timedelta(hours=1))
    with database.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = reader_acquisition_report(
            session, start=date(2026, 10, 1), end=date(2026, 10, 18), observed_at=OBSERVED
        )
        assert not session.new and not session.dirty and not session.deleted
    assert report["totals"] == {
        "verified_signups": 4,
        "activated": 2,
        "pending_window": 1,
        "matured_inactive": 1,
    }
    assert report["attribution_coverage"] == dict.fromkeys(
        ("attributed", "direct", "unknown", "unavailable"), 1
    )
    assert report["first_party_gaps"] == {
        "missing_signup_rows": 1,
        "missing_activation_rows": 1,
    }
    assert report["ga_sign_up"]["eligible"] == 2
    assert report["ga_sign_up"]["status_counts"] == {
        "unavailable": 2,
        "pending": 1,
        "attempted": 0,
        "http_accepted": 1,
        "failed": 0,
        "unknown": 0,
    }
    assert report["ga_reader_activation"]["eligible"] == 1
    assert report["ga_reader_activation"]["status_counts"]["unknown"] == 1
    assert report["ga_reader_activation"]["missing_measurement_rows"] == 1
    missing = next(row for row in report["groups"] if row["attribution_state"] == "unavailable")
    assert missing["acquisition_date"] is None and missing["source"] is None
    payload = json.dumps(report)
    for forbidden in (str(organic.id), organic.email, organic.subject, "123.456", "client_id"):
        assert forbidden not in payload


@pytest.mark.integration
def test_report_uses_utc_signup_bounds_and_keeps_distinct_acquisition_date(database):
    with database.begin() as session:
        start = datetime(2026, 10, 16, tzinfo=UTC)
        included = account(session, start)
        record_verified_signup(
            session,
            included.id,
            start,
            context("direct", start - timedelta(days=2), measurable=False),
        )
        reading(session, account(session, start - timedelta(microseconds=1)), start)
        account(session, start + timedelta(days=1))
    with database() as session:
        report = reader_acquisition_report(
            session, start=date(2026, 10, 16), end=date(2026, 10, 17), observed_at=OBSERVED
        )
    assert report["totals"]["verified_signups"] == 1
    assert report["groups"][0]["acquisition_date"] == "2026-10-14"


@pytest.mark.integration
def test_empty_report_is_reconciled_and_account_deletion_removes_cohort(database):
    with database.begin() as session:
        user = account(session, OBSERVED - timedelta(days=1))
        record_verified_signup(
            session, user.id, user.reader_signup_at, context("direct", OBSERVED, measurable=False)
        )
        reading(session, user, user.reader_signup_at + timedelta(hours=1))
    with database.begin() as session:
        session.delete(session.get(UserAccount, user.id))
    with database() as session:
        assert session.scalars(select(ReaderSignup)).all() == []
        assert session.scalars(select(ReaderMilestoneDelivery)).all() == []
        report = reader_acquisition_report(
            session, start=date(2026, 10, 1), end=date(2026, 10, 18), observed_at=OBSERVED
        )
    assert report["groups"] == []
    assert report["totals"] == dict.fromkeys(
        ("verified_signups", "activated", "pending_window", "matured_inactive"), 0
    )
    assert sum(report["ga_sign_up"]["status_counts"].values()) == 0
