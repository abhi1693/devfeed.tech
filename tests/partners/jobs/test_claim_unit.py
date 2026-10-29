"""Contended claims release all transaction ownership before a bounded retry."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_claim
from sqlalchemy.exc import OperationalError


class DatabaseFailure(Exception):
    def __init__(self, state):
        self.sqlstate = state


def claim_environment(monkeypatch, *, catalog=(True,)):
    state = SimpleNamespace(clock=0.0, active=False, events=[], attempts=0)
    locks = iter(catalog)
    session = Mock()
    session.execute.return_value.scalar_one.side_effect = lambda: next(locks)

    @contextmanager
    def begin():
        state.active = True
        state.attempts += 1
        try:
            yield session
        except BaseException:
            state.events.append("rollback")
            raise
        else:
            state.events.append("commit")
        finally:
            state.active = False

    def sleep(seconds):
        assert not state.active, "Retry slept while retaining transaction ownership"
        state.events.append("sleep")
        state.clock += seconds

    factory = Mock()
    factory.begin.side_effect = begin
    monkeypatch.setattr(
        partner_claim,
        "time",
        SimpleNamespace(monotonic=lambda: state.clock, sleep=Mock(side_effect=sleep)),
    )
    return state, factory, session


def test_claim_enters_the_body_once_after_canonical_catalog_acquisition(monkeypatch):
    state, factory, session = claim_environment(monkeypatch)
    selected = object()

    def acquire(current):
        assert current is session and state.active
        statements = [str(call.args[0]) for call in session.execute.call_args_list]
        assert statements == [
            "SET LOCAL lock_timeout = '100ms'",
            "SELECT pg_try_advisory_xact_lock(734819025)",
        ]
        return selected

    with partner_claim.claim_partner_job(factory, acquire) as (current, claimed):
        assert current is session and claimed is selected
        assert state.active
    assert state.events == ["commit"]
    assert state.attempts == 1


def test_busy_catalog_retries_without_attempting_rows_or_holding_locks(monkeypatch):
    state, factory, session = claim_environment(monkeypatch, catalog=(False, False, True))
    acquire = Mock(return_value="claimed")
    with partner_claim.claim_partner_job(factory, acquire) as (_, selected):
        assert selected == "claimed"
    acquire.assert_called_once_with(session)
    assert state.events == ["rollback", "sleep", "rollback", "sleep", "commit"]
    assert state.attempts == 3


def test_unavailable_row_retries_the_whole_claim_after_rollback(monkeypatch):
    state, factory, session = claim_environment(monkeypatch, catalog=(True, True))
    acquire = Mock(side_effect=[OperationalError("claim", {}, DatabaseFailure("55P03")), "claimed"])
    with partner_claim.claim_partner_job(factory, acquire) as (_, selected):
        assert selected == "claimed"
    assert acquire.call_args_list[0].args == (session,)
    assert acquire.call_count == 2
    assert state.events == ["rollback", "sleep", "commit"]


@pytest.mark.parametrize(
    "where", ["between-attempts", "during-acquisition", "successful-acquisition"]
)
def test_claim_deadline_preserves_the_durable_job_for_later_recovery(monkeypatch, where):
    state, factory, _ = claim_environment(monkeypatch, catalog=(False, False))
    monkeypatch.setattr(partner_claim, "CLAIM_TIMEOUT_SECONDS", 0.1)
    acquire = Mock()
    if where != "between-attempts":
        state, factory, _ = claim_environment(monkeypatch)

        def blocked(_session):
            state.clock += 0.2
            if where == "during-acquisition":
                raise OperationalError("claim", {}, DatabaseFailure("55P03"))
            return "claimed-too-late"

        acquire.side_effect = blocked
    with (
        pytest.raises(partner_claim.PartnerClaimTimeout, match="remained contended"),
        partner_claim.claim_partner_job(factory, acquire),
    ):
        pytest.fail("A contended claim entered the mutation body")
    assert not state.active
    assert state.attempts == (2 if where == "between-attempts" else 1)
    if where == "between-attempts":
        acquire.assert_not_called()
    else:
        partner_claim.time.sleep.assert_not_called()


@pytest.mark.parametrize("state_code", ["40P01", None])
def test_other_database_errors_propagate_without_retry(monkeypatch, state_code):
    state, factory, _ = claim_environment(monkeypatch)
    failure = OperationalError("claim", {}, DatabaseFailure(state_code))
    acquire = Mock(side_effect=failure)
    with (
        pytest.raises(OperationalError) as caught,
        partner_claim.claim_partner_job(factory, acquire),
    ):
        pytest.fail("A failed claim entered the mutation body")
    assert caught.value is failure and state.events == ["rollback"]
    acquire.assert_called_once()
    partner_claim.time.sleep.assert_not_called()


@pytest.mark.parametrize("kind", ["row-lock", "catalog-busy", "runtime"])
def test_mutation_body_errors_are_never_retried_or_yielded_twice(monkeypatch, kind):
    state, factory, _ = claim_environment(monkeypatch)
    failure = {
        "row-lock": OperationalError("body", {}, DatabaseFailure("55P03")),
        "catalog-busy": partner_claim._ClaimBusy(),
        "runtime": RuntimeError("mutation failed"),
    }[kind]
    acquire = Mock(return_value=None)
    with pytest.raises(type(failure)) as caught, partner_claim.claim_partner_job(factory, acquire):
        raise failure
    assert caught.value is failure
    assert state.events == ["rollback"]
    acquire.assert_called_once()
    partner_claim.time.sleep.assert_not_called()
