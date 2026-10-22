"""Notification consumers validate destinations and recover selected deliveries safely.

Repository slices are supplied read outcomes. These tests assert recovery policy
and payload preservation, not SQL lease selection, locking or uniqueness.
"""

import copy
import hashlib
import json
import uuid
from contextlib import nullcontext
from datetime import timedelta
from functools import lru_cache
from unittest.mock import Mock

import pytest
from devfeed_core import notification_delivery, notifications
from devfeed_core.models import ArticleEnrichmentJob, NotificationDelivery
from devfeed_core.notification_config import ChimelySettings
from devfeed_notifications import config
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_source_readiness_unit import NOW, configuration, job


@pytest.mark.parametrize(
    "url",
    [
        "https://user:private@chimely.example",
        "https://chimely.example/inbox",
        "ftp://chimely.example",
    ],
)
def test_enabled_notification_destinations_require_an_origin_without_embedded_credentials(url):
    with pytest.raises(ValidationError, match=r"HTTP\(S\) origin without credentials") as failure:
        ChimelySettings(
            _env_file=None,
            notifications_enabled=True,
            chimely_api_url=url,
            chimely_admin_environment="admin-production",
        )
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("environment", ["replace-me", "admin production", "a" * 101])
def test_enabled_notification_consumers_reject_placeholder_or_invalid_environment_slugs(
    environment,
):
    with pytest.raises(ValidationError, match="explicit environment slug"):
        ChimelySettings(
            _env_file=None,
            notifications_enabled=True,
            chimely_api_url="https://chimely.example",
            chimely_admin_environment=environment,
        )


def test_notification_service_settings_are_cached_until_explicit_reload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # An isolated cache cannot replace another app's loaded consumer settings.
    load = lru_cache()(config.get_settings.__wrapped__)
    monkeypatch.setattr(config, "get_settings", load)
    monkeypatch.setenv("DEVFEED_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_CHIMELY_API_URL", "https://chimely.example")
    monkeypatch.setenv("DEVFEED_CHIMELY_ADMIN_ENVIRONMENT", "admin-production")
    monkeypatch.setenv("DEVFEED_CHIMELY_ADMIN_API_KEY", "disposable-admin-key")
    monkeypatch.delenv("DEVFEED_CHIMELY_USER_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEVFEED_CHIMELY_USER_API_KEY", raising=False)
    first = config.get_settings()
    monkeypatch.setenv("DEVFEED_CHIMELY_ADMIN_API_KEY", "rotated-admin-key")
    assert config.get_settings() is first
    assert first.chimely_admin_api_key.get_secret_value() == "disposable-admin-key"
    load.cache_clear()
    second = config.get_settings()
    assert (
        second is not first
        and second.chimely_admin_api_key.get_secret_value() == "rotated-admin-key"
    )
    assert "rotated-admin-key" not in repr(second)


def test_disabled_notification_consumer_starts_without_environment_or_credentials():
    settings = config.Settings(
        _env_file=None,
        notifications_enabled=False,
        chimely_api_url=None,
        chimely_admin_environment=None,
        chimely_user_environment=None,
        chimely_admin_api_key=None,
        chimely_user_api_key=None,
    )
    assert not settings.notifications_enabled
    assert settings.chimely_api_url is settings.chimely_admin_api_key is None


def selected_delivery(*, attempts=1, age=timedelta(days=2)):
    event = "release:" + str(uuid.uuid4())
    return NotificationDelivery(
        id=uuid.uuid4(),
        event_key=event,
        dedup_key=hashlib.sha256(json.dumps(["admin", None, event]).encode()).hexdigest(),
        audience="admin",
        category="product.release",
        payload={"title": "Release", "body": "A release is available.", "action_url": "/"},
        status="running",
        attempts=attempts,
        created_at=NOW - age,
        available_at=NOW - timedelta(minutes=15),
        dispatched_at=NOW - timedelta(minutes=10),
        lease_until=NOW - timedelta(minutes=1),
        lease_token=uuid.uuid4(),
    )


def test_delivery_recovery_applies_policy_to_each_selected_run_without_replacing_payloads():
    rows = [
        selected_delivery(),
        selected_delivery(age=timedelta(days=28)),
        selected_delivery(attempts=20),
    ]
    before = [(row.id, row.dedup_key, copy.deepcopy(row.payload)) for row in rows]
    session, factory = Mock(spec=Session), Mock()
    session.scalars.return_value.all.return_value = rows
    factory.begin.return_value = nullcontext(session)
    assert notification_delivery.recover_notifications(factory, 25, NOW) == 3
    assert [row.status for row in rows] == ["queued", "failed", "failed"]
    assert rows[0].available_at == NOW + timedelta(seconds=30)
    assert [row.finished_at for row in rows] == [None, NOW, NOW]
    for row, retained in zip(rows, before, strict=True):
        assert (row.id, row.dedup_key, row.payload) == retained
        assert row.lease_token is row.lease_until is row.dispatched_at is None
        assert row.error == "Delivery worker interrupted; lease recovered"
    session.add.assert_not_called()


def test_empty_selected_delivery_slice_has_no_recovery_effects():
    session, factory = Mock(spec=Session), Mock()
    session.scalars.return_value.all.return_value = []
    factory.begin.return_value = nullcontext(session)
    assert notification_delivery.recover_notifications(factory, 25, NOW) == 0
    session.add.assert_not_called()


def test_delivery_retry_wrapper_retains_identity_and_honors_the_upstream_retry_hint(monkeypatch):
    current = selected_delivery()
    identifier = current.id
    monkeypatch.setattr(notification_delivery, "utcnow", lambda: NOW)
    notification_delivery.retry_delivery(current, "Upstream is unavailable", retry_after=120)
    assert current.id == identifier and current.status == "queued"
    assert current.available_at == NOW + timedelta(seconds=120)
    assert current.error == "Upstream is unavailable" and current.lease_token is None


@pytest.mark.parametrize("enabled", [False, True])
def test_new_queued_work_does_not_emit_a_failure_or_completion_notification(monkeypatch, enabled):
    monkeypatch.setattr(
        notifications, "get_settings", lambda: configuration(notifications_enabled=enabled)
    )
    current = job(ArticleEnrichmentJob, article_id=uuid.uuid4(), result={}, changed_fields=[])
    connection = Mock()
    notifications.record_job_transition(None, connection, current)
    connection.scalar.assert_not_called()


def test_successful_metadata_enrichment_reports_the_change_count_without_private_values():
    current = job(
        ArticleEnrichmentJob,
        status="succeeded",
        article_id=uuid.uuid4(),
        changed_fields=["title", "summary"],
        result={"private": "saved publisher extraction"},
    )
    output = notifications.job_notification(current)
    assert output is not None and output.category == "jobs.article-enrichment.success"
    assert "Updated 2 metadata field(s)." in output.body
    assert "saved publisher extraction" not in output.model_dump_json()


@pytest.mark.parametrize("event_key", ["", "x" * 256])
def test_notification_producer_refuses_an_unpersistable_event_key_before_requesting_a_write(
    monkeypatch, event_key
):
    monkeypatch.setattr(
        notifications, "get_settings", lambda: configuration(notifications_enabled=True)
    )
    session = Mock(spec=Session)
    message = notifications.NotificationMessage(
        category="product.release", title="Release", body="New release", action_url="/"
    )
    with pytest.raises(ValueError, match="event key must be 1–255 characters"):
        notifications.enqueue_notification(session, event_key, message, audience="admin")
    session.connection.return_value.scalar.assert_not_called()
    session.commit.assert_not_called()


def test_enabled_producer_uses_its_existing_connection_and_returns_the_repository_receipt(
    monkeypatch,
):
    monkeypatch.setattr(
        notifications, "get_settings", lambda: configuration(notifications_enabled=True)
    )
    session, identifier = Mock(spec=Session), uuid.uuid4()
    session.connection.return_value.scalar.return_value = identifier
    message = notifications.NotificationMessage(
        category="product.release", title="Release", body="New release", action_url="/"
    )
    assert (
        notifications.enqueue_notification(session, "release:2026-10", message, audience="admin")
        == identifier
    )
    session.connection.assert_called_once_with()
    session.commit.assert_not_called()
