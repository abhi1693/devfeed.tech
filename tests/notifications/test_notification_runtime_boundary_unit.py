"""Delivery entry points recheck eligibility and ownership without leaking routing IDs."""

import hashlib
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from devfeed_core import feed_notifications, notifications
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import NotificationDelivery
from devfeed_notifications import delivery
from devfeed_notifications.config import Settings

from tests.support.source_readiness import NOW


@pytest.fixture
def runtime(monkeypatch):
    identifier = uuid.uuid4()
    current = NotificationDelivery(
        id=identifier,
        event_key="release:current",
        dedup_key=hashlib.sha256(
            json.dumps(["admin", None, "release:current"]).encode()
        ).hexdigest(),
        audience="admin",
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        category="product.update",
        payload={"title": "New release", "body": "An update is available.", "action_url": "/"},
        error=None,
    )
    settings = Settings(
        _env_file=None,
        notifications_enabled=True,
        chimely_api_url="http://chimely.internal:8080",
        chimely_admin_environment="admin-env",
        chimely_admin_api_key="disposable-admin-key",
        chimely_user_environment="user-env",
        chimely_user_api_key="disposable-reader-key",
    )
    session, active, requests = Mock(), [], []
    session.scalar.return_value = current

    @contextmanager
    def begin():
        assert not active
        active.append(True)
        try:
            yield session
        finally:
            active.pop()

    provider = Mock(return_value=NS(begin=begin))
    monkeypatch.setattr(delivery, "session_factory", provider)
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)
    clock = NS(now=NOW)
    monkeypatch.setattr(delivery, "utcnow", lambda: clock.now)
    on_request = Mock()

    def response(request):
        assert not active
        requests.append(request)
        on_request(request)
        return httpx.Response(201, json={"id": "accepted"})

    client = httpx.Client
    monkeypatch.setattr(
        delivery.httpx,
        "Client",
        lambda **options: client(**options, transport=httpx.MockTransport(response)),
    )
    return NS(
        job=current,
        settings=settings,
        session=session,
        provider=provider,
        active=active,
        requests=requests,
        on_request=on_request,
        clock=clock,
    )


def test_disabled_delivery_waits_for_reenable_without_claiming_or_contacting_transport(runtime):
    runtime.settings.notifications_enabled = False
    delivery._deliver(runtime.job.id)
    runtime.provider.assert_not_called()
    assert runtime.job.status == "queued" and runtime.job.attempts == 0
    assert runtime.requests == []


@pytest.mark.parametrize("current", [False, True])
def test_followed_topic_delivery_rechecks_currentness_and_sends_only_subscriber_safe_payload(
    monkeypatch, runtime, current
):
    value = runtime
    user_id, article_id = uuid.uuid4(), uuid.uuid4()
    value.job.category, value.job.audience = "feed.topic.new", "user"
    value.job.subscriber_id = notifications.notification_subscriber_id(
        audience="user", issuer="https://identity.example", subject="reader", organization_id="org"
    )
    value.job.event_key = f"feed:{article_id}"
    value.job.dedup_key = hashlib.sha256(
        json.dumps(["user", value.job.subscriber_id, value.job.event_key]).encode()
    ).hexdigest()
    message = notifications.NotificationMessage(
        category=value.job.category,
        title="Angular routing",
        body="New from a topic or source you follow.",
        action_url="/articles/angular-routing",
    )
    value.job.payload = {
        **message.model_dump(exclude={"category"}),
        "article_id": str(article_id),
        "user_id": str(user_id),
    }
    eligible = Mock(return_value=current)
    monkeypatch.setattr(feed_notifications, "delivery_is_current", eligible)
    delivery._deliver(value.job.id)
    eligible.assert_called_once_with(value.session, value.job)
    assert value.job.status == "succeeded" and value.job.finished_at == NOW
    if not current:
        assert value.job.attempts == 0 and value.requests == []
        assert value.job.error == "Skipped: article or followed topic is no longer eligible"
    else:
        assert value.job.attempts == 1 and value.job.lease_token is value.job.lease_until is None
        [request] = value.requests
        payload = json.loads(request.content)
        assert payload["payload"] == message.model_dump(exclude={"category"})
        assert payload["subscriber_id"] == value.job.subscriber_id
        assert payload["idempotency_key"] == f"devfeed:{value.job.id}"
        assert (
            str(user_id) not in request.content.decode()
            and str(article_id) not in request.content.decode()
        )
        assert request.url.path == "/v1/notifications"
        assert request.headers["authorization"] == "Bearer disposable-reader-key"


def test_superseded_delivery_cannot_finish_its_replacements_running_attempt(runtime):
    value = runtime
    replacement = []

    def recover(request):
        from datetime import timedelta

        value.clock.now = NOW + timedelta(seconds=delivery.LEASE_SECONDS + 1)
        value.job.status, value.job.lease_token, value.job.lease_until = "queued", None, None
        value.job.available_at = value.clock.now
        replacement.append(start_job(value.job, value.clock.now, delivery.LEASE_SECONDS))

    value.on_request.side_effect = recover
    delivery._deliver(value.job.id)
    assert len(value.requests) == 1
    assert value.job.status == "running" and value.job.attempts == 2
    assert value.job.lease_token == replacement[0] and value.job.error is None
    assert value.job.finished_at is None


@pytest.mark.parametrize("failure", [None, "identifier", "database"])
def test_runtime_entry_binds_job_log_context_and_reraises_failures_for_worker_recovery(
    monkeypatch, runtime, caplog, failure
):
    phases = []

    @contextmanager
    def context(kind, identifier):
        phases.append((kind, identifier))
        try:
            yield
        finally:
            phases.append("closed")

    monkeypatch.setattr(delivery, "job_log_context", context)
    identifier = "not-a-job-id" if failure == "identifier" else str(runtime.job.id)
    if failure == "database":
        runtime.session.scalar.side_effect = RuntimeError("Database unavailable")
    if failure:
        with pytest.raises(ValueError if failure == "identifier" else RuntimeError):
            delivery.deliver_notification(identifier)
        assert runtime.job.status == "queued" and runtime.requests == []
        assert "notification_runtime_failed" in caplog.text
    else:
        assert delivery.deliver_notification(identifier) is None
        assert runtime.job.status == "succeeded" and len(runtime.requests) == 1
    assert phases == [("notifications", identifier), "closed"]
