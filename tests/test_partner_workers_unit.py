"""Partner worker lease fencing, bounded diagnostics and queue routing."""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_product_sync as products
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_aggregator import partner_tasks as evaluations
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import PartnerConnection, utcnow


def transaction_factory(session):
    factory = Mock(return_value=nullcontext(session))
    factory.begin.side_effect = lambda: nullcontext(session)
    return factory


def leased_job(**changes):
    return NS(
        **{
            "id": uuid.uuid4(),
            "product_id": uuid.uuid4(),
            "parent_id": uuid.uuid4(),
            "provider": "nick-launches",
            "operation": "sync_product",
            "external_id": "checker",
            "status": "queued",
            "attempts": 0,
            "payload": {},
            "snapshot": {"articles": []},
            "available_at": utcnow(),
            "lease_token": None,
            "lease_until": None,
            "dispatched_at": None,
            "finished_at": None,
            "error": None,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "mode", ["disabled", "missing", "stale", "exhausted", "success", "error", "lost", "changed"]
)
def test_evaluation_rejects_stale_or_unowned_results(monkeypatch, mode):
    session = Mock()
    factory = transaction_factory(session)
    job = leased_job(attempts=3 if mode == "exhausted" else 0)
    session.scalar.return_value = None if mode == "missing" else job
    monkeypatch.setattr(evaluations, "get_settings", lambda: NS(ai_enabled=mode != "disabled"))
    monkeypatch.setattr(evaluations, "lock_catalog", Mock())
    current = Mock(
        side_effect=[True, False] if mode == "changed" else None, return_value=mode != "stale"
    )
    monkeypatch.setattr(evaluations, "snapshot_current", current)
    validated = {"decisions": [{"relevant": True}]}
    monkeypatch.setattr(evaluations, "validate_result", lambda *_: validated)

    def assess(_):
        if mode == "error":
            raise RuntimeError("private provider response")
        if mode == "lost":
            job.lease_token = uuid.uuid4()
        return validated

    evaluations.process_evaluation(str(job.id), factory=factory, assessor=assess)
    if mode == "disabled":
        factory.begin.assert_not_called()
    elif mode == "missing":
        assert job.attempts == 0
    elif mode in {"stale", "exhausted", "changed"}:
        assert job.status == "failed" and job.lease_token is None
    elif mode == "success":
        assert job.status == "succeeded" and job.result == validated and job.lease_token is None
    elif mode == "error":
        assert job.status == "queued" and "private provider response" not in job.error
    else:
        assert job.status == "running" and not hasattr(job, "result")


@pytest.mark.parametrize("enabled,failed", [(False, False), (True, False), (True, True)])
def test_evaluation_dispatch_closes_queue_even_on_delivery_failure(monkeypatch, enabled, failed):
    session, queue = Mock(), Mock()
    job = leased_job()
    session.scalars.return_value.all.return_value = [job]
    monkeypatch.setattr(evaluations, "get_settings", lambda: NS(ai_enabled=enabled))
    monkeypatch.setattr(evaluations, "get_queue", lambda _: queue)
    monkeypatch.setattr(evaluations, "lock_catalog", Mock())
    if failed:
        queue.enqueue.side_effect = RuntimeError("redis unavailable")
        with pytest.raises(RuntimeError):
            evaluations.dispatch_partner_evaluations(transaction_factory(session))
        assert job.dispatched_at is None
    else:
        assert evaluations.dispatch_partner_evaluations(transaction_factory(session)) == int(
            enabled
        )
        assert queue.enqueue.call_count == int(enabled)
    assert queue.connection.close.call_count == int(enabled)


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "disabled",
        "parent-failed",
        "generation",
        "exhausted",
        "success",
        "identity",
        "http",
        "database",
        "lost",
        "paused",
    ],
)
def test_product_sync_preserves_generation_and_fences_results(monkeypatch, mode):
    session = Mock()
    factory = transaction_factory(session)
    candidate = {"external_id": "checker", "listing_url": "https://nicklaunches.com/checker"}
    job = leased_job(payload={"candidate": candidate}, attempts=3 if mode == "exhausted" else 0)
    parent = leased_job(
        id=job.parent_id,
        status="cancelled" if mode == "parent-failed" else "running",
        payload={"connection_revision": 1, "generation": str(uuid.uuid4())},
    )
    connection = NS(
        enabled=mode != "disabled",
        sync_revision=2 if mode == "generation" else 1,
        provider="nick-launches",
        connector={},
    )
    session.scalar.return_value = None if mode == "missing" else job
    session.get.side_effect = lambda model, identifier: (
        connection if model is PartnerConnection else parent if identifier == parent.id else job
    )
    monkeypatch.setattr(products, "lock_catalog", Mock())
    monkeypatch.setattr(pipeline, "pending_redirect_proofs", lambda *_: [])
    monkeypatch.setattr(products, "ProductCandidate", Mock())
    products.ProductCandidate.model_validate.return_value = NS(external_id="checker")
    canonical = NS(id=uuid.uuid4())
    writer = Mock(return_value=canonical)
    if mode == "database":
        writer.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(products, "upsert_partner_product", writer)

    def read(*_):
        if mode == "http":
            raise FeedError("private response", status=429, retryable=True, retry_after=120)
        if mode == "lost":
            job.lease_token = uuid.uuid4()
        if mode == "paused":
            connection.enabled = False
        return NS(provider="other" if mode == "identity" else job.provider, external_id="checker")

    products.process_product_sync(job.id, factory, product_reader=read)
    if mode == "missing":
        assert job.attempts == 0
    elif mode in {"disabled", "parent-failed", "generation", "exhausted", "identity", "paused"}:
        assert job.status == "failed" and job.lease_token is None
    elif mode == "success":
        assert job.status == "succeeded" and job.product_id == canonical.id
    elif mode in {"http", "database"}:
        assert job.status == "queued" and "private response" not in job.error
        if mode == "http":
            assert (job.available_at - utcnow()).total_seconds() > 110
    else:
        assert job.status == "running" and writer.call_count == 0


@pytest.mark.parametrize(
    "operation,enabled,failed",
    [
        ("sync", False, False),
        ("sync_product", True, False),
        ("assess", True, False),
        ("sync", True, True),
    ],
)
def test_pipeline_routes_api_work_to_standard_worker_and_closes_queue(
    monkeypatch, operation, enabled, failed
):
    session, queue = Mock(), Mock()
    job = leased_job(operation=operation)
    session.scalars.return_value.all.return_value = [job]
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(pipeline, "lock_catalog", Mock())
    monkeypatch.setattr(pipeline, "get_settings", lambda: NS(ai_enabled=enabled))
    lookup = Mock(return_value=queue)
    monkeypatch.setattr(pipeline, "get_queue", lookup)
    if failed:
        queue.enqueue.side_effect = RuntimeError("redis unavailable")
        with pytest.raises(RuntimeError):
            pipeline.dispatch_partner_pipeline(transaction_factory(session))
        assert job.dispatched_at is None
    else:
        assert pipeline.dispatch_partner_pipeline(transaction_factory(session)) == 1
        assert job.dispatched_at is not None
    expected = "source-analysis" if operation == "assess" else "ingestion"
    lookup.assert_called_once_with(expected)
    assert expected in queue.enqueue.call_args.kwargs["job_id"]
    queue.connection.close.assert_called_once()


@pytest.mark.parametrize("created", [False, True])
def test_scheduling_keeps_future_connections_and_qualifies_products(monkeypatch, created):
    from datetime import timedelta

    session = Mock()
    job = leased_job(operation="sync")
    due = NS(next_sync_at=utcnow() - timedelta(seconds=1))
    future = NS(next_sync_at=utcnow() + timedelta(hours=1))
    product = NS(id=uuid.uuid4())
    session.scalars.side_effect = [NS(all=lambda: [due, future]), NS(all=lambda: [product])]
    session.new = [job] if created else []
    request = Mock(return_value=job)
    assess = Mock()
    monkeypatch.setattr(pipeline, "request_sync", request)
    monkeypatch.setattr(pipeline, "request_assessment", assess)
    monkeypatch.setattr(pipeline, "lock_catalog", Mock())
    pipeline.schedule_partner_syncs(transaction_factory(session))
    request.assert_called_once_with(session, due)
    assess.assert_called_once_with(session, product)
    session.flush.assert_called_once()
