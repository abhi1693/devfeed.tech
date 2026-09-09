"""Notification domain and delivery behavior; no service or migration execution."""

import json
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from devfeed_core import notifications
from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, NotificationDelivery, utcnow
from devfeed_core.notification_config import ChimelySettings
from devfeed_notifications import delivery, dispatcher
from devfeed_notifications.config import Settings
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value


def message(**changes):
    return notifications.NotificationMessage(
        category="product.update", title="Update", body="New release", action_url="/", **changes
    )


@pytest.mark.parametrize("model", notifications.PIPELINES)
@pytest.mark.parametrize(
    "status,severity", [("failed", "error"), ("queued", "warning"), ("running", None)]
)
def test_all_job_types_report_problems_without_private_error_text(model, status, severity):
    job = model(
        id=uuid.uuid4(), attempts=2, status=status, error="https://secret:user@host/private"
    )
    event = notifications.job_notification(job)
    if severity:
        assert event.severity == severity
        assert event.category == f"jobs.{notifications.PIPELINES[model][0]}.{severity}"
        assert str(job.id) in event.action_url
        assert "secret" not in event.model_dump_json()
    else:
        assert event is None


@pytest.mark.parametrize("model", notifications.PIPELINES)
def test_unchanged_success_is_quiet_but_recovery_is_visible(model):
    job = model(id=uuid.uuid4(), attempts=1, status="succeeded")
    assert notifications.job_notification(job) is None
    job.attempts = 2
    assert notifications.job_notification(job).severity == "success"


def test_new_articles_notification_contains_counts():
    job = IngestionJob(
        id=uuid.uuid4(), attempts=1, status="succeeded", articles_created=10, entries_skipped=2
    )
    event = notifications.job_notification(job)
    assert "10 new articles" in event.body and "2 entries skipped" in event.body


def test_routing_and_idempotency_are_scoped_to_audience_and_recipient():
    statements = []
    connection = SimpleNamespace(scalar=lambda stmt: statements.append(stmt))
    for audience, recipient in [
        ("admin", None),
        ("user", None),
        ("user", "user_alice"),
        ("user", "user_bob"),
        ("user", "user_bob"),
    ]:
        notifications._insert(
            connection, "same-event", message(), audience=audience, subscriber_id=recipient
        )
    values = [stmt.compile(dialect=postgresql.dialect()).params for stmt in statements]
    assert len({item["dedup_key"] for item in values}) == 4
    assert values[-1]["dedup_key"] == values[-2]["dedup_key"]
    assert all("ON CONFLICT (dedup_key) DO NOTHING" in str(stmt) for stmt in statements)
    assert values[0]["audience"] == "admin" and values[1]["audience"] == "user"


def test_disabled_feature_never_opens_database():
    session = SimpleNamespace(connection=lambda: pytest.fail("Must not connect"))
    assert notifications.enqueue_notification(session, "event", message(), audience="user") is None


def test_orm_listener_ignores_unchanged_status_and_only_emits_admin_events(monkeypatch):
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    calls = []
    monkeypatch.setattr(notifications, "_insert", lambda *args, **kw: calls.append((args, kw)))
    job = IngestionJob(id=uuid.uuid4(), status="succeeded", attempts=1, articles_created=2)
    set_committed_value(job, "status", "succeeded")
    notifications.record_job_transition(None, None, job)
    assert not calls
    job.status = "failed"
    notifications.record_job_transition(None, None, job)
    assert calls[0][1] == {"audience": "admin"}


@pytest.mark.parametrize(
    "url", ["//evil", "https://evil", "javascript:alert(1)", "/\\evil", "/path?secret=x"]
)
def test_notification_actions_stay_inside_receiving_app(url):
    with pytest.raises(ValidationError):
        notifications.NotificationMessage(
            category="release", title="New", body="Release", action_url=url
        )


def test_identities_are_stable_and_separate_across_audiences_issuers_orgs():
    args = {
        "audience": "admin",
        "issuer": "https://idp",
        "subject": "alice",
        "organization_id": "org",
    }
    identifier = notifications.notification_subscriber_id(**args)
    assert identifier == notifications.notification_subscriber_id(**args)
    for change in ({"audience": "user"}, {"issuer": "https://other"}, {"organization_id": "other"}):
        assert identifier != notifications.notification_subscriber_id(**{**args, **change})


def test_recipient_cannot_cross_audiences():
    with pytest.raises(ValueError, match="audience"):
        notifications._insert(
            None, "event", message(), audience="admin", subscriber_id="user_alice"
        )


def settings(**changes):
    return Settings(
        _env_file=None,
        notifications_enabled=True,
        chimely_api_url="http://chimely.internal:8080",
        chimely_admin_environment="admin-prod",
        chimely_admin_api_key="admin-private-key",
        **changes,
    )


def test_environment_and_secrets_are_required_only_for_enabled_consumers():
    assert not ChimelySettings(_env_file=None).notifications_enabled
    with pytest.raises(ValidationError):
        ChimelySettings(_env_file=None, notifications_enabled=True)
    with pytest.raises(ValidationError, match="separate"):
        settings(chimely_user_environment="admin-prod", chimely_user_api_key="user-key")
    with pytest.raises(ValidationError, match="USER_API_KEY"):
        settings(chimely_user_environment="user-prod")
    with pytest.raises(ValidationError, match="different environment API keys"):
        settings(chimely_user_environment="user-prod", chimely_user_api_key="admin-private-key")


class JobStore:
    def __init__(self, job):
        self.job, self.in_transaction, self.statements = job, False, []

    @contextmanager
    def begin(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    def scalar(self, statement):
        self.statements.append(statement)
        return self.job


@pytest.fixture
def delivery_setup(monkeypatch):
    job = NotificationDelivery(
        id=uuid.uuid4(),
        event_key="one",
        dedup_key="a" * 64,
        audience="admin",
        status="queued",
        attempts=0,
        created_at=utcnow(),
        available_at=utcnow(),
        category="jobs.ingestion",
        payload={"title": "Completed"},
    )
    store = JobStore(job)
    monkeypatch.setattr(delivery, "session_factory", lambda: store)
    monkeypatch.setattr(delivery, "get_settings", lambda: settings())
    requests = []
    real_client = httpx.Client
    response = SimpleNamespace(status=201, headers={})

    def transport(request):
        assert not store.in_transaction, "Network call must not hold DB connection"
        requests.append(request)
        return httpx.Response(
            response.status, headers=response.headers, text="private-server-details"
        )

    monkeypatch.setattr(
        delivery.httpx,
        "Client",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(transport)),
    )
    return SimpleNamespace(job=job, store=store, requests=requests, response=response)


def test_delivery_is_claimed_committed_then_sent_and_replay_is_ignored(delivery_setup):
    state = delivery_setup
    delivery._deliver(state.job.id)
    assert state.job.status == "succeeded" and state.job.attempts == 1
    request = state.requests[0]
    assert request.url.path == "/v1/broadcasts"
    assert request.headers["Authorization"] == "Bearer admin-private-key"
    assert json.loads(request.content)["idempotency_key"] == f"devfeed:{state.job.id}"
    assert not state.store.statements[0]._for_update_arg.skip_locked
    delivery._deliver(state.job.id)
    assert len(state.requests) == 1


def test_retry_keeps_same_idempotency_key_and_honors_backoff(delivery_setup):
    state = delivery_setup
    state.response.status, state.response.headers = 429, {"retry-after": "120"}
    delivery._deliver(state.job.id)
    assert state.job.status == "queued" and state.job.attempts == 1
    assert (state.job.available_at - utcnow()).total_seconds() > 118
    assert "private" not in state.job.error
    state.job.available_at = utcnow()
    state.response.status = 200  # Chimely's idempotent replay success.
    delivery._deliver(state.job.id)
    assert state.job.status == "succeeded"
    assert json.loads(state.requests[0].content) == json.loads(state.requests[1].content)


def test_future_targeted_user_message_uses_only_user_environment_key(delivery_setup, monkeypatch):
    state = delivery_setup
    state.job.audience, state.job.subscriber_id = "user", "user_alice"
    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: settings(
            chimely_user_environment="users-prod", chimely_user_api_key="user-private-key"
        ),
    )
    delivery._deliver(state.job.id)
    request = state.requests[0]
    assert request.url.path == "/v1/notifications"
    assert request.headers["Authorization"] == "Bearer user-private-key"
    assert json.loads(request.content)["subscriber_id"] == "user_alice"


def test_missing_user_configuration_never_falls_back_to_admin(delivery_setup):
    state = delivery_setup
    state.job.audience = "user"
    delivery._deliver(state.job.id)
    assert not state.requests and state.job.status == "queued"


@pytest.mark.parametrize("age,attempts", [(29, 0), (0, 19)])
def test_expired_or_exhausted_deliveries_require_manual_review(delivery_setup, age, attempts):
    state = delivery_setup
    state.job.created_at -= timedelta(days=age)
    state.job.attempts, state.response.status = attempts, 503
    delivery._deliver(state.job.id)
    assert state.job.status == "failed" and state.job.finished_at
    assert len(state.requests) == (0 if age else 1)


def test_dispatcher_uses_common_rq_task_path_and_stamps_only_after_enqueue(delivery_setup):
    state = delivery_setup
    calls = []

    def enqueue(*args, **kw):
        assert state.store.in_transaction and state.job.dispatched_at is None
        calls.append(args)

    count = dispatcher.dispatch(state.store, SimpleNamespace(enqueue=enqueue), 1, utcnow())
    assert count == 1 and state.job.dispatched_at
    assert calls == [("devfeed_notifications.delivery.deliver_notification", str(state.job.id))]


@pytest.mark.parametrize(
    "queue_name, expected",
    [
        ("all", ["ingestion", "analysis", "notifications"]),
        ("background", ["ingestion", "notifications"]),
    ],
)
def test_common_workers_consume_enabled_queues_fairly(monkeypatch, queue_name, expected):
    from devfeed_aggregator import worker
    from rq.worker import DequeueStrategy

    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "codex_app_server_url", "ws://127.0.0.1:4500")
    queues, calls, closed = [], [], []

    def queue(name="ingestion"):
        result = SimpleNamespace(
            name=name, connection=SimpleNamespace(close=lambda: closed.append(name))
        )
        queues.append(result)
        return result

    def factory(selected, **kwargs):
        assert [q.name for q in selected] == expected
        return SimpleNamespace(name="common-worker", work=lambda **kw: calls.append(kw))

    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(worker, "Worker", factory)
    worker.run(burst=True, queue_name=queue_name)
    assert get_settings().ai_enabled  # Producers must still create analysis jobs.
    assert calls[0]["dequeue_strategy"] == DequeueStrategy.ROUND_ROBIN
    assert closed == expected


def test_relationship_research_has_its_own_event_preferences():
    from devfeed_core.models import TopicAnalysisJob

    job = TopicAnalysisJob(
        id=uuid.uuid4(), topic_id=uuid.uuid4(), attempts=1, status="succeeded", outcome="enriched"
    )
    event = notifications.job_notification(job)
    assert event.category == "jobs.relationship-research.success"
    assert event.action_url == f"/jobs/analysis/topics/{job.id}"
