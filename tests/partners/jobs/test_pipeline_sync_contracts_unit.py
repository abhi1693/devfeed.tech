"""Partner import orchestration; supplied repository rows do not simulate SQL semantics."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import (
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
)
from devfeed_core.partner_connectors import connector_snapshot
from devfeed_core.partner_tools import ProductInput

from tests.discovery.test_discovery_review_contracts_unit import NOW
from tests.support.source_readiness import configuration


@pytest.fixture
def pipeline_case(monkeypatch):
    connection = PartnerConnection(
        provider="nick-launches",
        name="Launch platform",
        enabled=True,
        archived_at=None,
        sync_revision=1,
        revision=1,
        connector={},
        sync_interval_minutes=360,
        next_sync_at=NOW,
        last_sync_at=NOW - timedelta(days=1),
        last_error=None,
    )
    item = ProductInput(
        provider=connection.provider,
        external_id="api-checker",
        name="API Checker",
        product_url="https://checker.example/",
        listing_url="https://nicklaunches.com/products/checker/",
        tagline="Checks OpenAPI specifications for incompatible changes.",
        pricing="free",
    )
    product = PartnerProduct(
        id=uuid.uuid4(),
        name=item.name,
        product_url=item.product_url,
        tagline=item.tagline,
        pricing=item.pricing,
        status="pending",
        revision=1,
        excluded=False,
        merged_into_id=None,
        assessment_revision=0,
        assessment={"state": "checking", "reason": "Checking capabilities."},
        technologies=[],
        evidence=[],
        managed_logo={},
        managed_image={},
        updated_at=NOW,
    )
    listing = PartnerListing(
        id=uuid.uuid4(),
        provider=connection.provider,
        external_id=item.external_id,
        product_id=product.id,
        active=True,
        identity_status="resolved",
        name=item.name,
        product_url=item.product_url,
        listing_url=item.listing_url,
        tagline=item.tagline,
        pricing=item.pricing,
        attribution="Via launch platform",
    )
    job = PartnerPipelineJob(
        id=uuid.uuid4(),
        provider=connection.provider,
        operation="sync",
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        dispatched_at=NOW,
        payload={
            "pipeline_version": 2,
            "generation": str(uuid.uuid4()),
            "connection_revision": 1,
            "connector": connector_snapshot(connection),
            "cursor": None,
            "cursors": [],
            "pages": 0,
        },
    )
    session = Mock()
    state = SimpleNamespace(open=False, now=NOW)
    rows = {PartnerConnection: connection, PartnerPipelineJob: job, PartnerProduct: product}

    def scalar(statement):
        # Select each already-prepared repository row by entity; evaluate no predicates or locks.
        return rows.get(statement.column_descriptions[0]["entity"])

    session.scalar.side_effect = scalar

    def get(model, key):
        row = rows.get(model)
        identifier = (
            getattr(row, "provider", None)
            if model is PartnerConnection
            else getattr(row, "id", None)
        )
        return row if key == identifier else None

    session.get.side_effect = get
    session.execute.return_value.all.return_value = []

    @contextmanager
    def begin():
        assert not state.open
        state.open = True
        try:
            yield session
        finally:
            state.open = False

    factory = SimpleNamespace(begin=begin)
    monkeypatch.setattr(pipeline, "lock_catalog", Mock())
    monkeypatch.setattr(pipeline, "utcnow", lambda: state.now)
    monkeypatch.setattr(pipeline, "get_settings", lambda: configuration(ai_enabled=True))
    availability = Mock(return_value=True)
    monkeypatch.setattr(pipeline, "product_has_active_source", availability)
    withdrawn = Mock(return_value=0)
    monkeypatch.setattr(pipeline, "withdraw_missing_listings", withdrawn)
    return SimpleNamespace(
        connection=connection,
        product=product,
        listing=listing,
        item=item,
        job=job,
        session=session,
        factory=factory,
        state=state,
        rows=rows,
        availability=availability,
        withdrawn=withdrawn,
    )


def process(value, **kwargs):
    return pipeline._process_pipeline(str(value.job.id), factory=value.factory, **kwargs)


def test_sync_checkpoints_next_page_without_declaring_generation_complete(pipeline_case):
    value = pipeline_case
    original = copy.deepcopy(value.job.payload)

    def read(cursor):
        assert cursor is None and not value.state.open
        assert value.job.status == "running" and value.job.attempts == 1
        assert value.job.lease_until == NOW + timedelta(seconds=360)
        return [value.item], "page-2"

    process(value, reader=read)
    assert value.job.status == "queued" and value.job.attempts == 0
    assert value.job.available_at == NOW + timedelta(seconds=2)
    assert value.job.dispatched_at is value.job.lease_token is value.job.lease_until is None
    assert value.job.payload == {
        **original,
        "cursor": "page-2",
        "cursors": ["page-2"],
        "pages": 1,
        "discovery_complete": False,
    }
    assert value.connection.last_sync_at == NOW - timedelta(days=1)
    value.withdrawn.assert_not_called()


def test_last_page_waits_for_child_syncs_before_withdrawing_unseen_listings(pipeline_case):
    value = pipeline_case
    value.session.execute.return_value.all.return_value = [("queued", 1)]
    process(value, reader=lambda _: ([value.item], None))
    assert value.job.payload["discovery_complete"] and value.job.payload["pages"] == 1
    assert value.job.status == "queued" and value.job.attempts == 0
    assert value.job.available_at == NOW + timedelta(seconds=5)
    assert value.job.lease_token is None and value.job.finished_at is None
    value.withdrawn.assert_not_called()


@pytest.mark.parametrize(
    "barrier", ["disabled", "archived", "revision", "exhausted", "missing", "unclaimed"]
)
def test_import_guard_prevents_external_calls_for_unavailable_or_stale_work(pipeline_case, barrier):
    value = pipeline_case
    if barrier == "disabled":
        value.connection.enabled = False
    elif barrier == "archived":
        value.connection.archived_at = NOW
    elif barrier == "revision":
        value.connection.sync_revision = 2
    elif barrier == "exhausted":
        value.job.attempts = 3
    elif barrier == "missing":
        value.rows[PartnerPipelineJob] = None
    else:
        value.session.scalar.side_effect = lambda statement: (
            value.connection
            if statement.column_descriptions[0]["entity"] is PartnerConnection
            else None
        )
    reader = Mock()
    process(value, reader=reader)
    reader.assert_not_called()
    if barrier in {"missing", "unclaimed"}:
        assert value.job.status == "queued" and value.job.attempts == 0
    else:
        assert value.job.status == "failed" and value.job.lease_token is None
        assert (
            value.job.error
            == {
                "disabled": "Connection paused",
                "archived": "Connection paused",
                "revision": "Connection changed",
                "exhausted": "Automatic checks exhausted their retry limit",
            }[barrier]
        )
    if barrier == "exhausted":
        assert value.connection.last_error == value.job.error


def test_missing_import_api_snapshot_retries_without_external_calls(pipeline_case, monkeypatch):
    value = pipeline_case
    value.job.payload = {**value.job.payload, "connector": None}
    monkeypatch.setattr(pipeline, "connector_snapshot", lambda _connection: None)
    reader = Mock()

    process(value, reader=reader)

    reader.assert_not_called()
    assert value.job.status == "queued" and value.job.attempts == 1
    assert value.job.lease_token is value.job.lease_until is None
    assert value.job.available_at > NOW
    assert (
        value.job.error
        == value.connection.last_error
        == "Platform API returned invalid product data"
    )
    value.withdrawn.assert_not_called()


@pytest.mark.parametrize("mode", ["same", "seen", "page_cap"])
def test_nonadvancing_or_over_budget_pagination_preserves_catalog_and_safe_diagnostic(
    pipeline_case, mode
):
    value = pipeline_case
    value.job.payload = {
        **value.job.payload,
        "cursor": "page-2",
        "cursors": ["page-1", "page-2"],
        "pages": 2,
    }
    cursor = "page-2" if mode == "same" else "page-1" if mode == "seen" else "page-3"
    if mode == "page_cap":
        value.job.payload["connector"]["max_pages"] = 3
    process(value, reader=lambda _: ([value.item], cursor))
    assert value.job.status == "queued" and value.job.attempts == 1
    assert (
        value.job.error
        == value.connection.last_error
        == "Platform API returned invalid product data"
    )
    assert value.job.payload["cursor"] == "page-2" and value.job.payload["pages"] == 2
    assert value.listing.active and value.product.status == "pending"
    value.withdrawn.assert_not_called()


@pytest.mark.parametrize("retryable,status", [(True, 503), (False, 401), (True, None)])
def test_platform_errors_preserve_retry_after_and_never_persist_raw_response(
    pipeline_case, retryable, status, caplog
):
    value = pipeline_case
    reader = Mock(
        side_effect=FeedError(
            "user=private token=secret", retryable=retryable, status=status, retry_after=120
        )
    )
    process(value, reader=reader)
    expected = "Platform API could not be read" + (f" (HTTP {status})" if status else "")
    assert value.job.error == value.connection.last_error == expected
    assert value.job.status == ("queued" if retryable else "failed")
    assert value.job.lease_token is None
    if retryable:
        assert value.job.available_at == NOW + timedelta(seconds=120)
    assert "secret" not in caplog.text and "secret" not in str(value.job.payload)
    value.withdrawn.assert_not_called()


@pytest.mark.parametrize("change", ["paused", "archived", "revision", "lease"])
def test_connection_or_lease_changes_during_fetch_prevent_page_checkpoint(pipeline_case, change):
    value = pipeline_case
    before = copy.deepcopy(value.job.payload)

    def read(cursor):
        if change == "paused":
            value.connection.enabled = False
        elif change == "archived":
            value.connection.archived_at = NOW
        elif change == "revision":
            value.connection.sync_revision += 1
        else:
            value.state.now = NOW + timedelta(seconds=361)
            start_job(value.job, value.state.now, 360)
        return [value.item], "page-2"

    process(value, reader=read)
    assert value.job.payload == before and value.connection.last_sync_at == NOW - timedelta(days=1)
    assert value.job.status == ("running" if change == "lease" else "failed")
    if change != "lease":
        assert value.job.error == "Connection changed" and value.job.lease_token is None
    value.withdrawn.assert_not_called()


@pytest.mark.parametrize("states", [{"queued": 2}, {"running": 1}, {"failed": 2}, {"succeeded": 3}])
def test_finalize_uses_child_outcomes_without_replaying_successful_products(pipeline_case, states):
    value = pipeline_case
    value.job.payload = {**value.job.payload, "discovery_complete": True, "pages": 2}
    value.session.execute.return_value.all.return_value = list(states.items())
    reader = Mock()
    process(value, reader=reader)
    reader.assert_not_called()
    if states.get("queued") or states.get("running"):
        assert value.job.status == "queued" and value.job.available_at == NOW + timedelta(seconds=5)
        value.withdrawn.assert_not_called()
    elif states.get("failed"):
        assert value.job.status == "failed" and "2 product syncs failed" in value.job.error
        assert value.connection.last_error == value.job.error
        value.withdrawn.assert_not_called()
    else:
        assert value.job.status == "succeeded" and value.job.finished_at == NOW
        assert value.connection.last_sync_at == NOW and value.connection.last_error is None
        assert value.connection.next_sync_at == NOW + timedelta(minutes=360)
        assert value.job.payload["finalize_batches"] == 1
        value.withdrawn.assert_called_once_with(
            value.session, value.job.provider, uuid.UUID(value.job.payload["generation"]), limit=25
        )


def test_full_cleanup_batch_yields_without_reimporting_then_finishes_remaining_withdrawals(
    pipeline_case,
):
    value = pipeline_case
    value.job.payload = {
        **value.job.payload,
        "discovery_complete": True,
        "pages": 1,
        "withdrawn": 4,
    }
    value.withdrawn.side_effect = [25, 2]
    reader = Mock()
    process(value, reader=reader)
    assert value.job.status == "queued" and value.job.payload["withdrawn"] == 29
    assert value.job.attempts == 0 and value.job.lease_token is None
    assert value.connection.last_sync_at == NOW - timedelta(days=1)
    value.state.now = value.job.available_at
    process(value, reader=reader)
    assert value.job.status == "succeeded" and value.job.payload["withdrawn"] == 31
    assert value.job.finished_at == value.state.now
    assert value.job.payload["finalize_batches"] == 2
    reader.assert_not_called()


def test_changed_generation_cannot_finalize_or_withdraw_existing_listings(pipeline_case):
    value = pipeline_case
    value.job.payload = {**value.job.payload, "discovery_complete": True, "pages": 1}
    value.connection.sync_revision = 2
    process(value, reader=Mock())
    assert value.job.status == "failed" and value.job.error == "Connection changed"
    value.withdrawn.assert_not_called()
