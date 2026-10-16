"""Privacy and failure boundaries for optional verified-reader diagnostics."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from devfeed_core import reader_acquisition
from devfeed_core.models import ReaderMilestoneDelivery, ReaderSignup, UserAccount
from devfeed_user_api import accounts, auth, reader_measurement, reader_measurement_delivery
from devfeed_user_api.config import Settings
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from test_user_auth import begin, complete
from test_user_auth import oidc_app as oidc_app

NOW = datetime.now(UTC).replace(microsecond=0)
PRIVATE_VALUE = "reader-private-value@example.invalid"


def config(**overrides):
    return Settings(
        _env_file=None,
        **{
            "DEVFEED_READER_MEASUREMENT_ENABLED": True,
            "DEVFEED_ANALYTICS_ENABLED": True,
            "DEVFEED_EXTENSION_ANALYTICS_ENABLED": True,
            "GOOGLE_ANALYTICS_ID": "G-READERTEST",
            "DEVFEED_READER_GA_API_SECRET": "reader-test-secret",
            "DEVFEED_READER_ACQUISITION_CAMPAIGNS": ["launch"],
            **overrides,
        },
    )


def capture(**overrides):
    return {
        "version": 1,
        "status": "attributed",
        "source": "google",
        "medium": "organic",
        "campaign": "launch",
        "landing_route": "article",
        "captured_at": int(NOW.timestamp()),
        "client_platform": "web",
        "measurement": "eligible",
        "client_id": "12345.67890",
        "session_id": int(NOW.timestamp()),
        **overrides,
    }


def request(raw, *, cookie=False, extra_headers=()):
    field = (
        (b"cookie", ("__Host-devfeed_user_acquisition=" + quote(raw)).encode())
        if cookie
        else (reader_measurement.HEADER.encode(), raw.encode())
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [field, *extra_headers],
        }
    )


@pytest.mark.parametrize("cookie", [False, True], ids=["header", "cookie"])
@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        "{",
        json.dumps(capture(client_id=PRIVATE_VALUE)),
        json.dumps(capture(session_id=True)),
        json.dumps(capture(source=PRIVATE_VALUE)),
        json.dumps(capture(landing_route="/settings?token=" + PRIVATE_VALUE)),
        json.dumps(capture(email=PRIVATE_VALUE)),
        json.dumps(capture(captured_at=2**100)),
        "x" * 1537,
    ],
)
def test_malformed_or_personal_capture_is_unavailable_without_raising(raw, cookie):
    result = reader_measurement.capture_context(
        request(raw, cookie=cookie), config(), cookie=cookie
    )
    assert result["measurement"] != "eligible"
    assert result["status"] == "unavailable"
    assert PRIVATE_VALUE not in json.dumps(result)
    assert "client_id" not in result and "session_id" not in result


@pytest.mark.parametrize("cookie", [False, True], ids=["header", "cookie"])
@pytest.mark.parametrize("privacy", [(b"dnt", b"1"), (b"sec-gpc", b"1")])
def test_current_privacy_headers_override_an_eligible_cookie_or_header(cookie, privacy):
    result = reader_measurement.capture_context(
        request(json.dumps(capture()), cookie=cookie, extra_headers=[privacy]),
        config(),
        cookie=cookie,
    )
    assert result["measurement"] == "disabled"
    assert result["status"] == "unavailable"
    assert "client_id" not in result and "session_id" not in result


@pytest.mark.parametrize(
    "current", [None, capture(measurement="unavailable", client_id=None, session_id=None)]
)
def test_callback_does_not_reuse_frozen_measurement_when_current_permission_is_unavailable(current):
    current_request = request("") if current is None else request(json.dumps(current), cookie=True)
    result = reader_measurement.callback_context(current_request, config(), capture())
    assert result["measurement"] != "eligible"
    assert "client_id" not in result and "session_id" not in result


def test_callback_preserves_frozen_acquisition_instead_of_postlogin_route():
    latest = capture(
        status="direct", source="direct", medium="none", campaign=None, landing_route="home"
    )
    result = reader_measurement.callback_context(
        request(json.dumps(latest), cookie=True), config(), capture()
    )
    assert (result["source"], result["medium"], result["landing_route"]) == (
        "google",
        "organic",
        "article",
    )
    assert result["measurement"] == "eligible"


@pytest.mark.parametrize(
    "replacement", [{"client_id": "54321.09876"}, {"client_platform": "edge_extension"}]
)
def test_callback_client_switch_retains_acquisition_but_discards_frozen_ga_identity(replacement):
    result = reader_measurement.callback_context(
        request(json.dumps(capture(**replacement)), cookie=True), config(), capture()
    )
    assert (result["source"], result["medium"]) == ("google", "organic")
    assert result["measurement"] == "unavailable"
    assert "client_id" not in result and "session_id" not in result


@pytest.mark.parametrize(
    "record_kind", ["other-account", "malformed-json", "invalid-binding", "redis-down", "valid"]
)
def test_private_session_measurement_binding_cannot_cross_accounts(monkeypatch, record_kind):
    from devfeed_user_api import dependencies

    user_id = uuid.uuid4()
    binding = {"client_id": "12345.67890", "client_platform": "web"}
    record = {
        "user_id": str(uuid.uuid4()) if record_kind == "other-account" else str(user_id),
        "reader_measurement": [] if record_kind == "invalid-binding" else binding,
    }

    def get(key):
        if record_kind == "redis-down":
            raise RuntimeError("Private Redis failure")
        return "{" if record_kind == "malformed-json" else json.dumps(record)

    monkeypatch.setattr(dependencies, "get_redis", lambda: SimpleNamespace(get=get))
    bound_request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", ("__Host-devfeed_user_session=" + "a" * 43).encode())],
        }
    )
    assert reader_measurement.session_binding(bound_request, config(), user_id) == (
        binding if record_kind == "valid" else {}
    )


@pytest.fixture
def delivery_runtime(monkeypatch):
    row = ReaderMilestoneDelivery(
        user_id=uuid.uuid4(),
        event_name="sign_up",
        id=uuid.uuid4(),
        occurred_at=NOW - timedelta(seconds=5),
        status="pending",
        reason="ready",
        attempts=0,
        payload={
            "client_id": "12345.67890",
            "session_id": int(NOW.timestamp()),
            "client_platform": "web",
            "acquisition_status": "attributed",
            "source": "google",
            "medium": "organic",
            "campaign": "launch",
            "landing_route": "article",
        },
    )
    state = SimpleNamespace(transactions=0, requests=[])

    class Factory:
        @contextmanager
        def begin(self):
            state.transactions += 1
            try:
                yield SimpleNamespace(scalar=lambda statement: row)
            finally:
                state.transactions -= 1

    monkeypatch.setattr(reader_measurement_delivery, "session_factory", Factory)
    monkeypatch.setattr(reader_measurement_delivery, "utcnow", lambda: NOW)
    return row, state


@pytest.mark.parametrize(
    "response_status,expected",
    [
        (204, "http_accepted"),
        (200, "http_accepted"),
        (302, "failed"),
        (500, "failed"),
        (None, "unknown"),
    ],
)
def test_delivery_claim_commits_before_one_http_attempt_and_never_replays(
    monkeypatch, caplog, delivery_runtime, response_status, expected
):
    row, state = delivery_runtime
    # Extra legacy/private fields must never be forwarded, even if a stored row
    # is broader than the current producer's narrow contract.
    row.payload.update(email=PRIVATE_VALUE, user_id=str(row.user_id), access_token=PRIVATE_VALUE)
    original = httpx.Client

    def respond(outgoing):
        state.requests.append(outgoing)
        assert state.transactions == 0
        assert (row.status, row.attempts) == ("attempted", 1)
        if response_status is None:
            raise httpx.ReadTimeout("reader-test-secret " + PRIVATE_VALUE, request=outgoing)
        return httpx.Response(response_status, text="reader-test-secret " + PRIVATE_VALUE)

    monkeypatch.setattr(
        reader_measurement_delivery.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == expected
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert len(state.requests) == 1 and row.attempts == 1
    assert row.status == expected
    if expected == "http_accepted":
        assert row.reason == "ingestion_unverified"
    outgoing = json.loads(state.requests[0].content)
    assert outgoing["timestamp_micros"] == int(row.occurred_at.timestamp() * 1_000_000)
    assert outgoing["client_id"] == "12345.67890"
    assert PRIVATE_VALUE not in json.dumps(outgoing)
    assert str(row.user_id) not in json.dumps(outgoing)
    assert str(row.id) not in json.dumps(outgoing)
    assert "reader-test-secret" not in caplog.text and PRIVATE_VALUE not in caplog.text


@pytest.mark.parametrize(
    "status", ["attempted", "http_accepted", "failed", "unknown", "unavailable"]
)
def test_nonpending_delivery_is_never_claimed_again(monkeypatch, delivery_runtime, status):
    row, _ = delivery_runtime
    row.status, row.attempts = status, 1
    monkeypatch.setattr(
        reader_measurement_delivery.httpx, "Client", lambda **kwargs: pytest.fail("HTTP retried")
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert row.status == status and row.attempts == 1


def test_lost_finish_write_leaves_attempt_uncertain_without_another_http_request(
    monkeypatch, caplog, delivery_runtime
):
    row, state = delivery_runtime
    original = httpx.Client

    def finish(*args):
        raise SQLAlchemyError("reader-test-secret " + PRIVATE_VALUE)

    monkeypatch.setattr(reader_measurement_delivery, "_finish", finish)
    monkeypatch.setattr(
        reader_measurement_delivery.httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda outgoing: state.requests.append(outgoing) or httpx.Response(204)
            ),
            **kwargs,
        ),
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "unknown"
    )
    assert (row.status, row.attempts) == ("attempted", 1)
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert len(state.requests) == 1
    assert "reader-test-secret" not in caplog.text and PRIVATE_VALUE not in caplog.text


def test_unexpected_client_failure_does_not_escape_or_retry_a_claim(
    monkeypatch, caplog, delivery_runtime
):
    row, _ = delivery_runtime
    calls = []

    def broken_client(**kwargs):
        calls.append(kwargs)
        assert (row.status, row.attempts) == ("attempted", 1)
        raise RuntimeError(
            "https://provider.invalid/?api_secret=reader-test-secret " + PRIVATE_VALUE
        )

    monkeypatch.setattr(reader_measurement_delivery.httpx, "Client", broken_client)
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "unknown"
    )
    assert (row.status, row.attempts) == ("attempted", 1)
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert len(calls) == 1
    assert "reader-test-secret" not in caplog.text and PRIVATE_VALUE not in caplog.text
    assert "provider.invalid" not in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("acquisition_status", "invented_state"),
        ("source", "private_source"),
        ("medium", "private_medium"),
        ("campaign", "not_configured"),
        ("landing_route", "private_profile_path"),
        ("session_id", True),
        ("client_id", PRIVATE_VALUE),
    ],
)
def test_delivery_revalidates_configured_dimensions_before_http(
    monkeypatch, delivery_runtime, field, value
):
    row, _ = delivery_runtime
    row.payload[field] = value
    monkeypatch.setattr(
        reader_measurement_delivery.httpx,
        "Client",
        lambda **kwargs: pytest.fail("Private payload sent"),
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert (row.status, row.reason, row.attempts) == ("unavailable", "invalid_payload", 0)


@pytest.mark.parametrize("payload", [None, [], PRIVATE_VALUE])
def test_malformed_persisted_payload_gets_a_terminal_audit_status(
    monkeypatch, delivery_runtime, payload
):
    row, _ = delivery_runtime
    row.payload = payload
    monkeypatch.setattr(
        reader_measurement_delivery.httpx,
        "Client",
        lambda **kwargs: pytest.fail("Malformed payload sent"),
    )
    assert (
        reader_measurement_delivery.send_reader_milestone(
            config(), user_id=row.user_id, event_name=row.event_name
        )
        == "not_sent"
    )
    assert (row.status, row.reason, row.attempts) == ("unavailable", "invalid_payload", 0)


@pytest.mark.parametrize("current", [False, True], ids=["historical-repair", "current-open"])
def test_activation_repair_records_first_open_without_retroactive_measurement(current):
    user_id, article_id = uuid.uuid4(), uuid.uuid4()
    first_open = NOW if current else NOW - timedelta(hours=1)
    signup = SimpleNamespace(
        user_id=user_id,
        signup_at=NOW - timedelta(days=1),
        acquisition_status="attributed",
        source="google",
        medium="organic",
        campaign="launch",
        landing_route="article",
        activated_at=None,
        activation_article_id=None,
    )
    reading = SimpleNamespace(occurred_at=first_open, article_id=article_id)
    rows = iter([signup.signup_at, reading, signup.signup_at])
    statements = []
    session = SimpleNamespace(
        scalar=lambda statement: next(rows),
        get=lambda model, identifier: signup,
        execute=statements.append,
        flush=lambda: None,
    )
    context = capture()
    arguments = dict(
        bound_client_id=context["client_id"],
        bound_platform="web",
        current_open_at=NOW,
        current_article_id=article_id if current else uuid.uuid4(),
    )
    assert reader_acquisition.record_reader_activation(session, user_id, context, **arguments)
    assert signup.activated_at == first_open and signup.activation_article_id == article_id
    assert not reader_acquisition.record_reader_activation(session, user_id, context, **arguments)
    assert len(statements) == 1
    delivery = statements[0].compile(dialect=postgresql.dialect()).params
    assert delivery["occurred_at"] == first_open
    if current:
        assert delivery["status"] == "pending" and delivery["payload"]["client_id"] == "12345.67890"
    else:
        assert delivery["status"] == "unavailable" and delivery["reason"] == "historical_activation"
        assert delivery["payload"] == {}


@pytest.mark.integration
def test_committed_signup_survives_failed_session_write_without_duplicate_retry(
    database, oidc_app, monkeypatch
):
    from redis.exceptions import ConnectionError
    from sqlalchemy import select

    state = oidc_app
    state.settings.reader_measurement_enabled = state.settings.analytics_enabled = True
    monkeypatch.setattr(auth, "save_user_with_status", accounts.save_user_with_status)
    conversions = []
    monkeypatch.setattr(auth, "send_signup_conversion", lambda *args, **kw: conversions.append("x"))
    monkeypatch.setattr(auth, "send_reader_milestone", lambda *args, **kw: conversions.append("ga"))
    original_set = state.store.set

    def failing_session_set(key, value, ex):
        if key.startswith("devfeed:user:session:"):
            raise ConnectionError("Simulated Redis outage after account commit")
        return original_set(key, value, ex)

    state.client.cookies.set("__Host-devfeed_user_acquisition", quote(json.dumps(capture())))
    flow = begin(state)
    monkeypatch.setattr(state.store, "set", failing_session_set)
    failed = complete(state, flow)
    assert failed.headers["location"].endswith("/login?error=login_failed")
    with database() as session:
        account = session.scalar(select(UserAccount))
        signup = session.get(ReaderSignup, account.id)
        assert account.reader_signup_at is not None and signup.signup_at == account.reader_signup_at
        delivery = session.get(ReaderMilestoneDelivery, (account.id, "sign_up"))
        assert (delivery.status, delivery.attempts) == ("pending", 0)
        account_id, signup_time = account.id, signup.signup_at
    monkeypatch.setattr(state.store, "set", original_set)
    state.client.cookies.set("__Host-devfeed_user_acquisition", quote(json.dumps(capture())))
    assert complete(state, begin(state)).headers["location"].endswith("/")
    assert conversions == []
    with database() as session:
        assert len(session.scalars(select(UserAccount)).all()) == 1
        assert len(session.scalars(select(ReaderSignup)).all()) == 1
        assert len(session.scalars(select(ReaderMilestoneDelivery)).all()) == 1
        assert session.get(ReaderSignup, account_id).signup_at == signup_time


@pytest.mark.integration
def test_concurrent_dispatchers_commit_one_claim_before_a_single_http_attempt(
    database, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    user_id = uuid.uuid4()
    with database.begin() as session:
        session.add(
            UserAccount(
                id=user_id,
                issuer="https://reader.example",
                subject="concurrent-dispatch",
                organization_id="reader-organization",
                reader_signup_at=NOW,
            )
        )
        session.flush()
        reader_acquisition.record_verified_signup(session, user_id, NOW, capture())
    monkeypatch.setattr(reader_measurement_delivery, "session_factory", lambda: database)
    provider_started, release_provider = Event(), Event()
    requests = []
    original = httpx.Client

    def respond(outgoing):
        requests.append(outgoing)
        # A separate transaction must see the claim while HTTP is still in
        # progress. Holding an uncommitted row lock during I/O is insufficient.
        with database() as session:
            persisted = session.get(ReaderMilestoneDelivery, (user_id, "sign_up"))
            assert (persisted.status, persisted.attempts) == ("attempted", 1)
            assert persisted.attempted_at is not None and persisted.finished_at is None
        provider_started.set()
        assert release_provider.wait(timeout=10), "Test did not release the provider response"
        return httpx.Response(204)

    monkeypatch.setattr(
        reader_measurement_delivery.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    settings = config()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            reader_measurement_delivery.send_reader_milestone,
            settings,
            user_id=user_id,
            event_name="sign_up",
        )
        try:
            assert provider_started.wait(timeout=5), "First dispatcher did not enter the provider"
            second = workers.submit(
                reader_measurement_delivery.send_reader_milestone,
                settings,
                user_id=user_id,
                event_name="sign_up",
            )
            assert second.result(timeout=5) == "not_sent"
            assert not first.done(), "The second dispatcher must finish before HTTP completes"
        finally:
            release_provider.set()
        assert first.result(timeout=5) == "http_accepted"
    assert len(requests) == 1
    assert (
        reader_measurement_delivery.send_reader_milestone(
            settings, user_id=user_id, event_name="sign_up"
        )
        == "not_sent"
    )
    assert len(requests) == 1
    with database() as session:
        persisted = session.get(ReaderMilestoneDelivery, (user_id, "sign_up"))
        assert (persisted.status, persisted.reason, persisted.attempts) == (
            "http_accepted",
            "ingestion_unverified",
            1,
        )
        assert persisted.finished_at is not None
