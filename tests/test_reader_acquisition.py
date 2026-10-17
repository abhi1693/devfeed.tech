"""Verified reader signup, acquisition privacy and original-open milestones."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import quote

import pytest
from devfeed_core import reader_acquisition as acquisition
from devfeed_core.identity_accounts import register_verified_user
from devfeed_core.models import (
    Article,
    ReaderMilestoneDelivery,
    ReaderSignup,
    UserAccount,
    UserReadingEvent,
    utcnow,
)
from devfeed_user_api import accounts, auth, engagement
from devfeed_user_api.config import Settings
from devfeed_user_api.reader_measurement import capture_context
from sqlalchemy import select
from test_user_auth import begin, complete
from test_user_auth import oidc_app as oidc_app
from test_user_personalization import user_data as user_data


def context(**changes):
    return {
        "version": 1,
        "status": "attributed",
        "source": "google",
        "medium": "organic",
        "campaign": None,
        "landing_route": "article",
        "captured_at": int(utcnow().timestamp()),
        "client_platform": "web",
        "measurement": "eligible",
        "client_id": "123.456",
        "session_id": 123,
        **changes,
    }


def identity(subject="reader"):
    return {
        "issuer": "https://issuer.example",
        "subject": subject,
        "organization_id": "org",
        "name": "Reader",
        "email": "reader@example.com",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "person@example.com"},
        {"campaign": "a-private-person@example.com"},
        {"landing_route": "/settings/private"},
        {"session_id": True},
        {"client_id": "fingerprint"},
        {"client_id": "١٢٣.٤٥٦"},
        {"captured_at": 1},
        {"captured_at": 2**100},
        {"raw_referrer": "https://secret.example/person"},
        {"captured_at": "future"},
    ],
)
def test_untrusted_acquisition_becomes_unavailable(changes):
    # Compute relative time at execution, not collection: the full suite can
    # take longer than the five-minute clock-skew allowance before this runs.
    if changes.get("captured_at") == "future":
        changes = {"captured_at": int(utcnow().timestamp()) + 600}
    assert acquisition.sanitize_context(context(**changes))["status"] == "unavailable"


def test_campaigns_are_configured_labels_and_never_arbitrary_utm_values():
    captured = context(campaign="autumn_release")
    assert "campaign" not in acquisition.sanitize_context(captured)
    assert (
        acquisition.sanitize_context(captured, campaigns=["autumn_release"])["campaign"]
        == "autumn_release"
    )


def test_login_freezes_initial_acquisition_and_binds_actual_measurement(oidc_app, monkeypatch):
    state = oidc_app
    state.settings.reader_measurement_enabled = state.settings.analytics_enabled = True
    saved, sent = [], []
    monkeypatch.setattr(
        auth,
        "save_user_with_status",
        lambda user: saved.append(dict(user)) or ("00000000-0000-4000-8000-000000000001", True),
    )
    monkeypatch.setattr(auth, "send_signup_conversion", lambda *a, **kw: None)
    monkeypatch.setattr(auth, "send_reader_milestone", lambda *a, **kw: sent.append(kw))
    state.client.cookies.set("__Host-devfeed_user_acquisition", quote(json.dumps(context())))
    flow = begin(state)
    state.client.cookies.set(
        "__Host-devfeed_user_acquisition",
        quote(json.dumps(context(source="bing", landing_route="home"))),
    )
    response = complete(state, flow)
    assert response.headers["location"].endswith("/")
    assert saved[0]["_reader_signup"]["source"] == "google"
    assert sent == [{"user_id": "00000000-0000-4000-8000-000000000001", "event_name": "sign_up"}]
    token = state.client.cookies.get("__Host-devfeed_user_session")
    record = json.loads(state.store.get(auth.key("session", token)))
    assert record["reader_measurement"] == {"client_id": "123.456", "client_platform": "web"}
    assert "reader_measurement" not in state.client.get("/v1/user/auth/me").json()
    assert "_reader_signup" not in record
    assert any(
        value.startswith("__Host-devfeed_user_acquisition=") and "max-age=0" in value.lower()
        for value in response.headers.get_list("set-cookie")
    )


@pytest.mark.parametrize("current", [None, {"bad": "payload"}, context(client_id="999.888")])
def test_missing_invalid_or_switched_callback_measurement_does_not_break_auth(
    oidc_app, monkeypatch, current
):
    state = oidc_app
    state.settings.reader_measurement_enabled = state.settings.analytics_enabled = True
    saved = []
    monkeypatch.setattr(
        auth,
        "save_user_with_status",
        lambda user: saved.append(dict(user)) or ("00000000-0000-4000-8000-000000000001", False),
    )
    state.client.cookies.set("__Host-devfeed_user_acquisition", quote(json.dumps(context())))
    flow = begin(state)
    if current is None:
        state.client.cookies.delete("__Host-devfeed_user_acquisition")
    else:
        state.client.cookies.set("__Host-devfeed_user_acquisition", quote(json.dumps(current)))
    assert complete(state, flow).headers["location"].endswith("/")
    assert saved[0]["_reader_signup"]["measurement"] != "eligible"
    assert "client_id" not in saved[0]["_reader_signup"]


@pytest.mark.parametrize("header", ["dnt", "sec-gpc"])
def test_server_respects_privacy_signal_even_with_eligible_cookie(header):
    settings = Settings(
        _env_file=None,
        DEVFEED_READER_MEASUREMENT_ENABLED=True,
        DEVFEED_ANALYTICS_ENABLED=True,
    )
    request = type(
        "Request",
        (),
        {
            "cookies": {"__Host-devfeed_user_acquisition": quote(json.dumps(context()))},
            "headers": {header: "1"},
        },
    )()
    assert capture_context(request, settings, cookie=True)["measurement"] == "disabled"


def reader(database, monkeypatch, *, signup_at=None, subject="reader", measured=True):
    moment = signup_at or utcnow() - timedelta(seconds=1)
    monkeypatch.setattr(accounts, "utcnow", lambda: moment)
    captured = context() if measured else acquisition.unavailable_context()
    identifier, created = accounts.save_user_with_status(
        {**identity(subject), "_reader_signup": captured}
    )
    assert created
    return uuid.UUID(identifier), moment


def reading(session, user_id, moment, *, source="article_open", article_id=None):
    identifier = article_id or uuid.uuid4()
    session.add(
        UserReadingEvent(
            user_id=user_id,
            article_id=identifier,
            read_date=moment.date(),
            occurred_at=moment,
            source=source,
        )
    )
    session.flush()
    return identifier


@pytest.mark.integration
def test_only_explicit_verified_callback_marks_signup_and_returning_login_never_rewrites(
    database, monkeypatch
):
    generic = uuid.UUID(accounts.save_user({**identity("generic"), "_reader_signup": context()}))
    materialized = uuid.UUID(register_verified_user(identity("administrator")))
    identifier, moment = reader(database, monkeypatch)
    assert accounts.save_user_with_status(
        {**identity(), "_reader_signup": context(source="bing")}
    ) == (str(identifier), False)
    with database() as session:
        assert session.get(UserAccount, generic).reader_signup_at is None
        assert session.get(UserAccount, materialized).reader_signup_at is None
        signup = session.get(ReaderSignup, identifier)
        assert signup.signup_at == moment and signup.source == "google"
        assert len(session.scalars(select(ReaderSignup)).all()) == 1
        assert session.get(ReaderMilestoneDelivery, (identifier, "sign_up")).status == "pending"


@pytest.mark.integration
def test_callback_races_create_one_signup_and_one_diagnostic(database):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: accounts.save_user_with_status(
                    {**identity(), "_reader_signup": context()}
                ),
                range(4),
            )
        )
    assert len({row[0] for row in results}) == 1 and sum(row[1] for row in results) == 1
    with database() as session:
        assert len(session.scalars(select(ReaderSignup)).all()) == 1
        assert len(session.scalars(select(ReaderMilestoneDelivery)).all()) == 1


@pytest.mark.integration
def test_measurement_failure_preserves_committed_marker_and_account(database, monkeypatch):
    def fail(*args):
        raise RuntimeError("simulated optional measurement error")

    monkeypatch.setattr(accounts, "record_verified_signup", fail)
    identifier, _ = reader(database, monkeypatch)
    with database() as session:
        assert session.get(UserAccount, identifier).reader_signup_at is not None
        assert session.get(ReaderSignup, identifier) is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "offset,expected",
    [
        (timedelta(microseconds=-1), False),
        (timedelta(0), True),
        (timedelta(days=7, microseconds=-1), True),
        (timedelta(days=7), False),
    ],
)
def test_activation_uses_exact_half_open_seven_day_event_window(
    database, monkeypatch, offset, expected
):
    identifier, signup_at = reader(database, monkeypatch, signup_at=utcnow() - timedelta(days=8))
    moment = signup_at + offset
    with database.begin() as session:
        article_id = reading(session, identifier, moment)
        assert (
            acquisition.record_reader_activation(
                session,
                identifier,
                context(),
                bound_client_id="123.456",
                bound_platform="web",
                current_open_at=moment,
                current_article_id=article_id,
            )
            is expected
        )
    with database() as session:
        signup = session.get(ReaderSignup, identifier)
        assert (signup.activated_at is not None) is expected
        delivery = session.get(ReaderMilestoneDelivery, (identifier, "reader_activation"))
        assert (delivery is not None) is expected


@pytest.mark.integration
def test_activation_is_one_original_open_and_never_reuses_signup_measurement_permission(
    database, monkeypatch
):
    identifier, signup_at = reader(database, monkeypatch)
    first = signup_at + timedelta(microseconds=1)
    with database.begin() as session:
        reading(session, identifier, first, source="preview")
        assert not acquisition.record_reader_activation(session, identifier, context())
        article_id = reading(session, identifier, first + timedelta(microseconds=1))
        assert acquisition.record_reader_activation(
            session,
            identifier,
            acquisition.unavailable_context(),
            current_open_at=first + timedelta(microseconds=1),
            current_article_id=article_id,
        )
        assert not acquisition.record_reader_activation(
            session, identifier, context(), bound_client_id="123.456", bound_platform="web"
        )
    with database() as session:
        delivery = session.get(ReaderMilestoneDelivery, (identifier, "reader_activation"))
        assert delivery.status == "unavailable" and delivery.payload == {}
        assert session.get(ReaderSignup, identifier).activation_article_id == article_id


@pytest.mark.integration
def test_activation_requires_authenticated_session_linkage(database, monkeypatch):
    identifier, signup_at = reader(database, monkeypatch)
    moment = signup_at + timedelta(microseconds=1)
    with database.begin() as session:
        article_id = reading(session, identifier, moment)
        assert acquisition.record_reader_activation(
            session,
            identifier,
            context(client_id="999.888"),
            bound_client_id="123.456",
            bound_platform="web",
            current_open_at=moment,
            current_article_id=article_id,
        )
    with database() as session:
        delivery = session.get(ReaderMilestoneDelivery, (identifier, "reader_activation"))
        assert delivery.status == "unavailable" and delivery.reason == "unbound_client"


@pytest.mark.integration
def test_missing_signup_ledger_is_recovered_from_marker_without_retrospective_ga(
    database, monkeypatch
):
    identifier, signup_at = reader(database, monkeypatch)
    with database.begin() as session:
        session.delete(session.get(ReaderSignup, identifier))
    with database.begin() as session:
        earlier = signup_at + timedelta(microseconds=1)
        reading(session, identifier, earlier)
        assert acquisition.record_reader_activation(
            session,
            identifier,
            context(),
            bound_client_id="123.456",
            bound_platform="web",
            current_open_at=utcnow(),
            current_article_id=uuid.uuid4(),
        )
    with database() as session:
        assert session.get(ReaderSignup, identifier).acquisition_status == "unavailable"
        assert session.get(ReaderSignup, identifier).activated_at == earlier
        assert (
            session.get(ReaderMilestoneDelivery, (identifier, "reader_activation")).reason
            == "historical_activation"
        )


@pytest.mark.integration
def test_repeated_original_open_after_optout_does_not_redispatch_pending_activation(
    database, user_data, monkeypatch
):
    client, current, user_id, _, _ = user_data
    signup_at = utcnow() - timedelta(seconds=1)
    with database.begin() as session:
        session.get(UserAccount, user_id).reader_signup_at = signup_at
        acquisition.record_verified_signup(session, user_id, signup_at, context())
        article_id = session.scalar(select(Article.id).where(Article.title == "Article 000"))
    settings = Settings(
        _env_file=None,
        base_url="http://testserver",
        cookie_secure=False,
        DEVFEED_READER_MEASUREMENT_ENABLED=True,
        DEVFEED_ANALYTICS_ENABLED=True,
    )
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    monkeypatch.setattr(
        engagement,
        "session_binding",
        lambda *args: {"client_id": "123.456", "client_platform": "web"},
    )
    attempts = []
    # Simulate an initial delivery that never claims its pending row.
    monkeypatch.setattr(
        engagement, "send_reader_milestone", lambda *args, **kwargs: attempts.append(kwargs)
    )
    path = f"/v1/user/articles/{article_id}/open"
    assert (
        client.post(
            path,
            headers={
                "Origin": "http://testserver",
                "X-Devfeed-Reader-Measurement": json.dumps(context()),
            },
        ).status_code
        == 200
    )
    assert attempts == [{"user_id": str(user_id), "event_name": "reader_activation"}]
    assert (
        client.post(
            path,
            headers={
                "Origin": "http://testserver",
                "X-Devfeed-Reader-Measurement": json.dumps(
                    acquisition.unavailable_context(disabled=True)
                ),
                "DNT": "1",
            },
        ).status_code
        == 200
    )
    assert len(attempts) == 1
    with database() as session:
        delivery = session.get(ReaderMilestoneDelivery, (user_id, "reader_activation"))
        assert delivery.status == "pending" and delivery.attempts == 0
        assert delivery.payload["client_id"] == "123.456"
