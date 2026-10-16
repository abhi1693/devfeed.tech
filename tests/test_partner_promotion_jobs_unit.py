"""Policy saves and enrollment slices keep bounded, resumable checkpoints."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_promotions as promotions
from devfeed_core.models import PartnerAccount, PartnerProduct, PartnerPromotionEnrollment
from sqlalchemy.sql.dml import Delete, Insert
from sqlalchemy.sql.selectable import Select

NOW = datetime(2026, 10, 12, tzinfo=UTC)
ACCOUNT_ID = uuid.UUID(int=10)


def account():
    return PartnerAccount(id=ACCOUNT_ID, name="Partner", tier="platinum", status="active")


def product_rows(count, *, first=1, owner=None):
    owner = owner or account()
    return [
        (owner, PartnerProduct(id=uuid.UUID(int=first + index), name=f"Product {index}"))
        for index in range(count)
    ]


def checkpoint(**changes):
    return PartnerPromotionEnrollment(
        **{
            "account_id": ACCOUNT_ID,
            "product_cursor": None,
            "requested_at": NOW - timedelta(hours=1),
            "updated_at": NOW - timedelta(hours=1),
            "available_at": NOW,
            "sweep_started_at": None,
            **changes,
        }
    )


def factory_for(session):
    factory = Mock()
    factory.begin.side_effect = lambda: nullcontext(session)
    return factory


def request_session(rows, jobs, *, exists=True):
    """Apply enrollment DML to transient rows at the database write boundary."""
    session = Mock()
    session.scalar.return_value = ACCOUNT_ID if exists else None

    def execute(statement):
        if isinstance(statement, Select):
            assert statement._limit_clause.value <= promotions.PROMOTION_BATCH_SIZE + 1
            return Mock(all=lambda: rows)
        if isinstance(statement, Insert):
            values = statement.compile().params
            job = jobs[0] if jobs else checkpoint()
            for key, value in values.items():
                setattr(job, key, value)
            jobs[:] = [job]
        elif isinstance(statement, Delete):
            jobs.clear()
        else:
            raise AssertionError(f"Unexpected enrollment write: {type(statement).__name__}")
        return Mock()

    session.execute.side_effect = execute
    return session


@pytest.mark.parametrize("count", [0, promotions.PROMOTION_BATCH_SIZE])
def test_small_policy_save_finishes_immediately_and_removes_obsolete_job(monkeypatch, count):
    rows = product_rows(count)
    jobs = [checkpoint(product_cursor=uuid.UUID(int=999))]
    session = request_session(rows, jobs)
    enrolled = Mock(return_value=count)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enrolled)
    monkeypatch.setattr(promotions, "_next_campaign_start", lambda *_: None)

    assert promotions.request_promotion_enrollment(session, ACCOUNT_ID, NOW) == count
    assert jobs == []
    assert enrolled.call_args.args[1] == rows


def test_large_policy_save_resets_old_cursor_and_defers_every_product(monkeypatch):
    rows = product_rows(promotions.PROMOTION_BATCH_SIZE + 1)
    jobs = [
        checkpoint(
            product_cursor=uuid.UUID(int=199),
            sweep_started_at=NOW - timedelta(minutes=5),
            available_at=NOW + timedelta(minutes=1),
        )
    ]
    session = request_session(rows, jobs)
    enrolled = Mock()
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enrolled)

    assert promotions.request_promotion_enrollment(session, ACCOUNT_ID, NOW) == 0
    enrolled.assert_not_called()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.product_cursor is None
    assert job.sweep_started_at is None
    assert job.available_at == job.requested_at == job.updated_at == NOW


def test_small_policy_save_keeps_a_checkpoint_for_future_campaign(monkeypatch):
    jobs = []
    rows = product_rows(2)
    due = NOW + timedelta(hours=2)
    session = request_session(rows, jobs)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", lambda *_: 2)
    monkeypatch.setattr(promotions, "_next_campaign_start", lambda *_: due)

    assert promotions.request_promotion_enrollment(session, ACCOUNT_ID, NOW) == 2
    assert len(jobs) == 1
    assert jobs[0].available_at == due
    assert jobs[0].product_cursor is None


def test_policy_save_does_not_create_work_for_a_deleted_account():
    jobs = []
    session = request_session([], jobs, exists=False)
    assert promotions.request_promotion_enrollment(session, ACCOUNT_ID, NOW) == 0
    session.execute.assert_not_called()
    assert jobs == []


@pytest.mark.parametrize("mode", ["no-account", "no-job", "not-due"])
def test_worker_skips_absent_or_no_longer_due_work(monkeypatch, mode):
    session = Mock()
    job = checkpoint(available_at=NOW + timedelta(seconds=1) if mode == "not-due" else NOW)
    session.scalar.side_effect = (
        [None] if mode == "no-account" else [account(), None if mode == "no-job" else job]
    )
    enrolled = Mock()
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enrolled)

    assert promotions.process_promotion_enrollment(factory_for(session), now=NOW) == 0
    enrolled.assert_not_called()
    session.execute.assert_not_called()
    session.delete.assert_not_called()


def test_zero_worker_limit_does_not_open_a_transaction():
    factory = Mock()
    assert promotions.process_promotion_enrollment(factory, limit=0, now=NOW) == 0
    factory.begin.assert_not_called()


@pytest.mark.parametrize("limit", [1, promotions.PROMOTION_BATCH_SIZE])
def test_worker_enrolls_one_bounded_page_and_checkpoints_last_processed_product(monkeypatch, limit):
    session = Mock()
    prior_cursor = uuid.UUID(int=50)
    started = NOW - timedelta(minutes=2)
    job = checkpoint(product_cursor=prior_cursor, sweep_started_at=started)
    rows = product_rows(limit + 1, first=51)
    session.scalar.side_effect = [rows[0][0], job]

    def candidates(statement):
        assert statement._limit_clause.value <= limit + 1
        assert prior_cursor in statement.compile().params.values()
        return Mock(all=lambda: rows)

    session.execute.side_effect = candidates
    enrolled = Mock(return_value=limit)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enrolled)

    assert (
        promotions.process_promotion_enrollment(factory_for(session), limit=limit, now=NOW) == limit
    )
    assert enrolled.call_args.args[1] == rows[:limit]
    assert job.product_cursor == rows[limit - 1][1].id
    assert job.sweep_started_at == started
    assert job.updated_at == NOW
    session.delete.assert_not_called()


@pytest.mark.parametrize("count", [0, 3])
def test_worker_completes_only_after_the_bounded_query_is_exhausted(monkeypatch, count):
    session = Mock()
    job = checkpoint()
    rows = product_rows(count)
    session.scalar.side_effect = [account(), job]
    session.execute.return_value.all.return_value = rows
    enrolled = Mock(return_value=count)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enrolled)
    monkeypatch.setattr(promotions, "_next_campaign_start", lambda *_: None)

    assert promotions.process_promotion_enrollment(factory_for(session), limit=3, now=NOW) == count
    assert enrolled.call_args.args[1] == rows
    assert job.sweep_started_at == job.updated_at == NOW
    session.delete.assert_called_once_with(job)


@pytest.mark.parametrize("campaign_delay", [timedelta(minutes=-1), timedelta(hours=1)])
def test_completed_sweep_restarts_for_campaigns_starting_during_or_after_scan(
    monkeypatch, campaign_delay
):
    session = Mock()
    sweep_start = NOW - timedelta(minutes=5)
    job = checkpoint(product_cursor=uuid.UUID(int=99), sweep_started_at=sweep_start)
    session.scalar.side_effect = [account(), job]
    session.execute.return_value.all.return_value = []
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", lambda *_: 0)
    due = NOW + campaign_delay

    def next_start(_session, account_id, after, current):
        assert account_id == ACCOUNT_ID
        assert after == sweep_start
        assert current == NOW
        return due

    monkeypatch.setattr(promotions, "_next_campaign_start", next_start)
    assert promotions.process_promotion_enrollment(factory_for(session), now=NOW) == 0
    assert job.product_cursor is job.sweep_started_at is None
    assert job.available_at == max(NOW, due)
    session.delete.assert_not_called()


@pytest.mark.parametrize("account_busy", [False, True])
def test_failed_slice_cools_down_only_its_request_generation(monkeypatch, account_busy):
    session = Mock()
    job = checkpoint()
    request_generation = job.requested_at
    session.scalar.side_effect = [account(), job, None if account_busy else ACCOUNT_ID]
    session.execute.return_value.all.return_value = product_rows(1)
    monkeypatch.setattr(
        promotions, "_enroll_automatic_rows", Mock(side_effect=RuntimeError("database unavailable"))
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        promotions.process_promotion_enrollment(factory_for(session), now=NOW)
    assert session.execute.call_count == 1 + int(not account_busy)
    if not account_busy:
        cooldown = session.execute.call_args.args[0].compile().params
        assert request_generation in cooldown.values()
        assert cooldown["available_at"] == NOW + timedelta(seconds=60)
        assert cooldown["updated_at"] == NOW
    session.delete.assert_not_called()
