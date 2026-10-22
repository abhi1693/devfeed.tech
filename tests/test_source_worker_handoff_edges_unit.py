"""Source deliveries preserve active ownership and defer browser work durably."""

import copy

import pytest
import test_source_lifecycle_commands_unit as lifecycle_fixtures
import test_worker_source_contracts_unit as worker_fixtures
from devfeed_aggregator import solver_jobs, source_tasks
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import SourceDiscoveryJob
from devfeed_core.services import OperationConflict
from test_source_lifecycle_commands_unit import advance, candidate
from test_source_readiness_unit import NOW, configuration, job

readiness_runtime = lifecycle_fixtures.runtime
source_worker = worker_fixtures.runtime


def test_duplicate_source_delivery_does_not_fetch_or_apply_work_held_by_another_owner(
    source_worker,
):
    source_worker.claim.return_value = None
    before = copy.deepcopy(
        (source_worker.job.status, source_worker.job.attempts, source_worker.job.lease_token)
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    source_worker.lookup.assert_not_called()
    source_worker.owned.assert_not_called()
    assert (
        source_worker.job.status,
        source_worker.job.attempts,
        source_worker.job.lease_token,
    ) == before


def test_unclassified_transport_failure_keeps_safe_retry_state_and_its_attempt_budget(
    source_worker,
):
    source_worker.lookup.side_effect = FeedError(
        "private resolver details", reason="transport_error", retryable=True
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    assert source_worker.job.status == "queued" and source_worker.job.attempts == 1
    assert source_worker.job.available_at > NOW and source_worker.job.lease_token is None
    assert source_worker.job.error == "Source enrichment failed: transport_error"
    assert source_worker.source.metadata_error == source_worker.job.error
    source_worker.reconcile.assert_not_called()


def test_browser_challenge_handoff_uses_the_real_durable_solver_receipt_without_spending_a_retry(
    source_worker, monkeypatch
):
    monkeypatch.setattr(
        solver_jobs,
        "get_settings",
        lambda: configuration(solver_queue_enabled=True, solver_services=[]),
    )
    monkeypatch.setattr(solver_jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(source_tasks, "defer_to_solver", solver_jobs.defer_to_solver)
    source_worker.lookup.side_effect = FeedError(
        "private challenge body", reason="browser_challenge", status=403, retryable=True
    )

    source_tasks.enrich_source(str(source_worker.job.id))

    row = source_worker.job
    assert row.status == "queued" and row.requires_solver and row.attempts == 0
    assert row.available_at == NOW and row.dispatched_at is None and row.finished_at is None
    assert row.lease_token is row.lease_until is None
    assert "browser verification" in row.error and "private challenge" not in row.error
    assert source_worker.source.metadata_error == row.error
    source_worker.reconcile.assert_not_called()


def test_source_less_candidate_with_active_discovery_cannot_advance_into_other_work(
    readiness_runtime,
):
    value = readiness_runtime
    value.source, value.candidates = None, [candidate()]
    active = job(
        SourceDiscoveryJob,
        candidate_id=value.candidates[0].id,
        stage="discover",
        result={"background": True},
    )
    value.tasks["discovery"] = active
    assert value.facts().readiness.next_action is None
    before = copy.deepcopy((active.status, active.attempts, active.result))

    with pytest.raises(OperationConflict):
        advance(value)

    assert (active.status, active.attempts, active.result) == before
    assert not any(command.called for command in value.commands)
