"""Typed publication and resumable audiences share one consent-safe transport."""

import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import http_ece
import pytest
from devfeed_core import push_notifications
from devfeed_core.db import get_engine
from devfeed_core.models import (
    DailyMustReadPush,
    Source,
    Topic,
    UserAccount,
    UserSource,
    UserTopic,
    WebPushDelivery,
    WebPushEvent,
    WebPushSubscription,
)
from devfeed_core.push_audience import PushAudience
from devfeed_core.push_notifications import enqueue_web_push, expand_web_push_events
from devfeed_core.push_types import PUSH_TYPES, PushType, WebPushMessage
from devfeed_notifications import web_push
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy import event as sqlalchemy_event
from test_daily_must_read_push import push_keys as push_keys
from test_web_push_audiences import NOW
from test_web_push_audiences import audience_data as audience_data

TEST_KIND = "test_only"


def make_message(**changes):
    return WebPushMessage(
        **{
            "kind": TEST_KIND,
            "title": "A useful server notification",
            "body": "A custom message used only by this test policy.",
            "action_url": "/articles/useful-article",
            "context": {"private_marker": "server-only context"},
            **changes,
        }
    )


def _validate_test_type(message, audience):
    if message.kind != TEST_KIND:
        raise ValueError("Incorrect test notification type")


def _prepare_test_type(session, event, user_id, now):
    return WebPushMessage.model_validate(event.payload)


@pytest.fixture
def registered_test_type(monkeypatch):
    # This policy exists only within its test. Production still registers one kind.
    monkeypatch.setitem(
        PUSH_TYPES, TEST_KIND, PushType(validate=_validate_test_type, prepare=_prepare_test_type)
    )


@pytest.fixture
def generic_data(audience_data, push_keys, registered_test_type, monkeypatch):
    data = audience_data
    with data.factory.begin() as session:
        # Audience-query fixtures use an earlier publication cutoff. These
        # publisher tests create events at NOW, so place late joins after NOW.
        session.get(UserAccount, data.users[8]).created_at = NOW + timedelta(seconds=1)
        session.execute(
            update(UserTopic)
            .where(UserTopic.user_id == data.users[10])
            .values(created_at=NOW + timedelta(seconds=1))
        )
        for subscription in session.scalars(select(WebPushSubscription)):
            subscription.p256dh, subscription.auth = push_keys.p256dh, push_keys.auth
            if subscription.user_id == data.users[9]:
                subscription.created_at = NOW + timedelta(seconds=1)
            subscription.allowed_kinds = (
                ["daily_must_read"]
                if subscription.user_id in {data.users[7], data.users[11]}
                else [TEST_KIND]
            )
    monkeypatch.setattr(push_notifications, "get_web_push_settings", lambda: push_keys.settings)
    monkeypatch.setattr(web_push, "get_settings", lambda: push_keys.settings)
    monkeypatch.setattr(web_push, "session_factory", lambda: data.factory)
    monkeypatch.setattr(
        web_push, "session_authorization_expires_at", lambda *args: NOW + timedelta(days=1)
    )
    monkeypatch.setattr(web_push, "utcnow", lambda: NOW)
    data.keys = push_keys
    return data


def publish(data, audience, *, key="test-event", message=None, expires_at=None):
    with data.factory.begin() as session:
        identifier = enqueue_web_push(
            session,
            event_key=key,
            message=message or make_message(),
            audience=audience,
            expires_at=expires_at or NOW + timedelta(hours=1),
            now=NOW,
        )
    assert identifier is not None
    return identifier


def deliveries(data, identifier):
    with data.factory() as session:
        return list(
            session.scalars(
                select(WebPushDelivery)
                .where(WebPushDelivery.event_id == identifier)
                .order_by(WebPushDelivery.user_id, WebPushDelivery.id)
            )
        )


def test_production_registry_contains_only_the_existing_daily_behavior():
    assert set(PUSH_TYPES) == {"daily_must_read"}


def test_unknown_types_cannot_publish_even_when_the_feature_is_disabled():
    with pytest.raises(ValueError, match="Unsupported browser notification type"):
        enqueue_web_push(
            Mock(),
            event_key="unknown",
            message=make_message(kind="unregistered"),
            audience=PushAudience.all(),
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"action_url": "https://another.test/article"},
        {"action_url": "//another.test/article"},
        {"action_url": "/articles/../settings"},
        {"action_url": "/articles/article?token=private"},
        {"action_url": "/articles/article#private"},
        {"title": " "},
        {"title": "x" * 201},
        {"body": "x" * 1001},
        {"context": []},
        {"kind": "UPPERCASE"},
        {"unexpected_recipient": "another-account"},
    ],
)
def test_message_contract_rejects_unsafe_actions_and_invalid_content(changes):
    with pytest.raises(ValidationError):
        make_message(**changes)


@pytest.mark.parametrize(
    "invalid",
    [
        "empty_key",
        "blank_key",
        "long_key",
        "expired",
        "past",
        "naive_expiry",
        "naive_now",
        "large_payload",
    ],
)
def test_publication_guards_run_without_touching_database(
    registered_test_type, monkeypatch, invalid
):
    monkeypatch.setattr(
        push_notifications, "get_web_push_settings", lambda: NS(web_push_enabled=False)
    )
    session = Mock()
    arguments = dict(
        event_key="valid-key",
        message=make_message(),
        audience=PushAudience.all(),
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    if invalid.endswith("key"):
        arguments["event_key"] = {"empty_key": "", "blank_key": " ", "long_key": "x" * 256}[invalid]
    elif invalid == "expired":
        arguments["expires_at"] = NOW
    elif invalid == "past":
        arguments["expires_at"] = NOW - timedelta(seconds=1)
    elif invalid == "naive_expiry":
        arguments["expires_at"] = (NOW + timedelta(hours=1)).replace(tzinfo=None)
    elif invalid == "naive_now":
        arguments["now"] = NOW.replace(tzinfo=None)
    else:
        arguments["message"] = make_message(context={"large": "x" * 3000})
    with pytest.raises(ValueError):
        enqueue_web_push(session, **arguments)
    assert session.mock_calls == []


def test_disabled_feature_does_not_publish_or_contact_database(registered_test_type, monkeypatch):
    monkeypatch.setattr(
        push_notifications, "get_web_push_settings", lambda: NS(web_push_enabled=False)
    )
    session = Mock()
    assert (
        enqueue_web_push(
            session,
            event_key="disabled",
            message=make_message(),
            audience=PushAudience.all(),
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        is None
    )
    assert session.mock_calls == []


@pytest.mark.parametrize("change", ["all", "many", "other_user", "body", "action"])
def test_daily_kind_cannot_be_used_for_broadcasts_or_custom_messages(change):
    user_id = uuid.UUID(int=1)
    message = make_message(
        kind="daily_must_read",
        body="Your personalized must-read for today.",
        context={"claim_id": str(uuid.uuid4()), "user_id": str(user_id)},
    )
    audience = PushAudience.users(user_id)
    if change == "all":
        audience = PushAudience.all()
    elif change == "many":
        audience = PushAudience.users(user_id, uuid.UUID(int=2))
    elif change == "other_user":
        audience = PushAudience.users(uuid.UUID(int=2))
    elif change == "body":
        message = message.model_copy(update={"body": "An unrelated custom announcement"})
    else:
        message = message.model_copy(update={"action_url": "/settings"})
    with pytest.raises(ValueError):
        enqueue_web_push(
            Mock(),
            event_key="daily-misuse",
            message=message,
            audience=audience,
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )


@pytest.mark.integration
def test_publication_rolls_back_with_its_business_transaction(generic_data):
    data = generic_data
    with (
        pytest.raises(RuntimeError, match="business transaction failed"),
        data.factory.begin() as session,
    ):
        identifier = enqueue_web_push(
            session,
            event_key="rollback",
            message=make_message(),
            audience=PushAudience.users(data.users[0]),
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
        assert identifier is not None
        raise RuntimeError("business transaction failed")
    with data.factory() as session:
        assert session.scalar(select(func.count()).select_from(WebPushEvent)) == 0
        assert session.scalar(select(func.count()).select_from(WebPushDelivery)) == 0


@pytest.mark.integration
@pytest.mark.parametrize("collision", ["title", "audience", "expiry"])
def test_event_keys_are_idempotent_and_reject_changed_immutable_content(generic_data, collision):
    data = generic_data
    audience = PushAudience.users(data.users[0], data.users[1])
    identifier = publish(data, audience)
    assert (
        publish(data, PushAudience.users(data.users[1], data.users[0], data.users[0])) == identifier
    )
    changes = (
        {"message": make_message(title="A different notification")}
        if collision == "title"
        else {"audience": PushAudience.users(data.users[0])}
        if collision == "audience"
        else {"expires_at": NOW + timedelta(hours=2)}
    )
    with pytest.raises(ValueError, match="already used"):
        publish(data, **{"audience": audience, **changes})
    with data.factory() as session:
        assert session.scalar(select(func.count()).select_from(WebPushEvent)) == 1
        assert session.get(WebPushEvent, identifier).payload["title"] == make_message().title


@pytest.mark.integration
@pytest.mark.parametrize("target", ["one", "many", "all", "segment"])
def test_registered_type_fans_out_to_only_consented_audience_browsers(generic_data, target):
    data = generic_data
    audience = {
        "one": PushAudience.users(data.users[0]),
        "many": PushAudience.users(data.users[2], data.users[0], data.users[2]),
        "all": PushAudience.all(),
        "segment": PushAudience.segment(topic_ids=data.topics, source_ids=data.sources),
    }[target]
    expected = {
        "one": [1],
        "many": [1, 3],
        "all": [1, 2, 3, 4, 5, 11, 13],
        "segment": [1, 2, 3, 13],
    }[target]
    identifier = publish(data, audience)
    assert expand_web_push_events(data.factory, now=NOW) == len(expected)
    jobs = deliveries(data, identifier)
    assert {job.user_id for job in jobs} == {data.users[index - 1] for index in expected}
    assert len(jobs) == len(expected) + (13 in expected)
    assert len({job.subscription_id for job in jobs}) == len(jobs)
    with data.factory() as session:
        assert session.get(WebPushEvent, identifier).expanded_at == NOW
        assert session.scalar(select(DailyMustReadPush)) is None


@pytest.mark.integration
def test_cursor_pages_resume_and_replayed_pages_do_not_duplicate_deliveries(generic_data):
    data = generic_data
    identifier = publish(data, PushAudience.all())
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 2
    assert {job.user_id for job in deliveries(data, identifier)} == set(data.users[:2])
    with data.factory() as session:
        stored = session.get(WebPushEvent, identifier)
        assert stored.recipient_cursor == data.users[1] and stored.expanded_at is None
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 2
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 2
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 1
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 0
    before = {job.id for job in deliveries(data, identifier)}
    with data.factory.begin() as session:
        stored = session.get(WebPushEvent, identifier)
        stored.recipient_cursor, stored.expanded_at = None, None
    assert expand_web_push_events(data.factory, now=NOW) == 7
    assert {job.id for job in deliveries(data, identifier)} == before


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason,user_index",
    [
        ("disabled", 6),
        ("expired", 7),
        ("wrong_kind", 8),
        ("late_account", 9),
        ("late_browser", 10),
        ("late_follow", 11),
        ("inactive_topic", 4),
        ("unapproved_source", 5),
    ],
)
def test_excluded_recipient_reason_never_creates_a_generic_delivery(
    generic_data, reason, user_index
):
    data = generic_data
    user_id = data.users[user_index - 1]
    audience = PushAudience.users(user_id)
    with data.factory() as session:
        account = session.get(UserAccount, user_id)
        subscription = session.scalar(
            select(WebPushSubscription).where(WebPushSubscription.user_id == user_id)
        )
        if reason == "disabled":
            assert not subscription.enabled
        elif reason == "expired":
            assert subscription.authorization_expires_at <= NOW
        elif reason == "wrong_kind":
            assert subscription.allowed_kinds == ["daily_must_read"]
        elif reason == "late_account":
            assert account.created_at > NOW and subscription.created_at < NOW
        elif reason == "late_browser":
            assert account.created_at < NOW and subscription.created_at > NOW
        elif reason == "late_follow":
            membership = session.get(UserTopic, (user_id, data.topics[0]))
            assert membership.created_at > NOW
            assert account.created_at < NOW and subscription.created_at < NOW
            audience = PushAudience.segment(topic_ids=[data.topics[0]])
        elif reason == "inactive_topic":
            assert session.get(Topic, data.topics[2]).status != "active"
            audience = PushAudience.segment(topic_ids=[data.topics[2]])
        else:
            assert session.get(Source, data.sources[1]).approval_status != "approved"
            audience = PushAudience.segment(source_ids=[data.sources[1]])
    identifier = publish(data, audience)
    expanded = expand_web_push_events(data.factory, now=NOW)
    jobs = deliveries(data, identifier)
    assert user_id not in {job.user_id for job in jobs}
    if reason == "late_follow":
        # Existing eligible followers remain; the new follower cannot join them.
        assert expanded == 2
        assert {job.user_id for job in jobs} == {data.users[0], data.users[12]}
    else:
        assert expanded == 0 and jobs == []


@pytest.mark.integration
def test_late_enrollment_and_rotated_consent_do_not_join_an_earlier_publication(generic_data):
    data = generic_data
    identifier = publish(data, PushAudience.all())
    with data.factory.begin() as session:
        first = session.scalar(
            select(WebPushSubscription).where(WebPushSubscription.user_id == data.users[0])
        )
        # The browser API rotates the consent identity and resets this cutoff on
        # re-enrollment, even when the relay endpoint is unchanged.
        first.created_at = NOW + timedelta(seconds=1)
        first.consent_id = uuid.uuid4()
        first.session_hash = "new-session-for-same-endpoint"
    assert expand_web_push_events(data.factory, now=NOW + timedelta(seconds=2)) == 6
    actual = {job.user_id for job in deliveries(data, identifier)}
    assert actual == {data.users[index - 1] for index in [2, 3, 4, 5, 11, 13]}
    assert not actual.intersection({data.users[0], data.users[8], data.users[9]})


@pytest.mark.integration
def test_expired_events_and_removed_types_complete_without_expansion(generic_data):
    data = generic_data
    expired = publish(
        data, PushAudience.all(), key="expires", expires_at=NOW + timedelta(minutes=1)
    )
    removed = publish(data, PushAudience.all(), key="removed-kind")
    with data.factory.begin() as session:
        stored = session.get(WebPushEvent, removed)
        stored.kind = "unregistered"
        stored.payload = {**stored.payload, "kind": "unregistered"}
    later = NOW + timedelta(minutes=1)
    assert expand_web_push_events(data.factory, now=later) == 0
    assert deliveries(data, expired) == deliveries(data, removed) == []
    with data.factory() as session:
        assert session.get(WebPushEvent, expired).expanded_at == later
        assert session.get(WebPushEvent, removed).expanded_at == later


@pytest.mark.integration
def test_new_single_recipient_event_expands_before_a_partial_campaign(generic_data):
    data = generic_data
    campaign = publish(data, PushAudience.all(), key="campaign")
    assert expand_web_push_events(data.factory, batch=1, now=NOW) == 1
    personal = publish(data, PushAudience.users(data.users[2]), key="personal")
    assert expand_web_push_events(data.factory, batch=1, now=NOW) == 1
    assert len(deliveries(data, personal)) == 1
    assert len(deliveries(data, campaign)) == 1


@pytest.mark.integration
def test_concurrent_expansion_creates_one_delivery_per_event_and_browser(generic_data):
    data = generic_data
    identifier = publish(data, PushAudience.all())
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: expand_web_push_events(data.factory, now=NOW), range(2))) == 7
    assert len(deliveries(data, identifier)) == 8


@pytest.mark.integration
def test_delivery_insert_failure_rolls_back_the_recipient_cursor(generic_data):
    data = generic_data
    identifier = publish(data, PushAudience.all())

    def fail_delivery_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO web_push_deliveries"):
            raise RuntimeError("simulated outbox insert failure")

    engine = get_engine()
    sqlalchemy_event.listen(engine, "before_cursor_execute", fail_delivery_insert)
    try:
        with pytest.raises(RuntimeError, match="outbox insert failure"):
            expand_web_push_events(data.factory, batch=2, now=NOW)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", fail_delivery_insert)
    with data.factory() as session:
        stored = session.get(WebPushEvent, identifier)
        assert stored.recipient_cursor is stored.expanded_at is None
    assert deliveries(data, identifier) == []
    assert expand_web_push_events(data.factory, batch=2, now=NOW) == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "subscription_seconds,live_seconds,expected_seconds",
    [(86400, 86400, 3600), (900, 30, 30), (30, 900, 30)],
)
def test_generic_sender_encrypts_registered_messages_without_daily_claims(
    generic_data, monkeypatch, subscription_seconds, live_seconds, expected_seconds
):
    data = generic_data
    identifier = publish(data, PushAudience.users(data.users[0]))
    assert expand_web_push_events(data.factory, now=NOW) == 1
    job = deliveries(data, identifier)[0]
    with data.factory.begin() as session:
        session.get(WebPushSubscription, job.subscription_id).authorization_expires_at = (
            NOW + timedelta(seconds=subscription_seconds)
        )
    monkeypatch.setattr(
        web_push,
        "session_authorization_expires_at",
        lambda *args: NOW + timedelta(seconds=live_seconds),
    )
    sent = Mock(return_value=(201, 0))
    monkeypatch.setattr(web_push, "send_request", sent)
    web_push.deliver_web_push(str(job.id))
    assert sent.call_count == 1
    endpoint, headers, encrypted = sent.call_args.args
    plain = json.loads(
        http_ece.decrypt(
            encrypted,
            private_key=data.keys.receiver,
            auth_secret=base64.urlsafe_b64decode(data.keys.auth + "=" * (-len(data.keys.auth) % 4)),
            version="aes128gcm",
        )
    )
    assert endpoint.startswith("https://fcm.googleapis.com/")
    assert headers["Content-Encoding"] == "aes128gcm"
    assert int(headers["TTL"]) == expected_seconds
    assert plain["notification"]["title"] == make_message().title
    assert plain["notification"]["body"] == make_message().body
    assert plain["data"]["kind"] == TEST_KIND
    assert plain["data"]["notification_id"] == str(identifier)
    assert plain["data"]["expires_at"] == int(
        (NOW + timedelta(seconds=expected_seconds)).timestamp()
    )
    assert "server-only context" not in json.dumps(plain)
    with data.factory() as session:
        finished = session.get(WebPushDelivery, job.id)
        assert finished.status == "succeeded" and finished.accepted_at == NOW
        assert session.scalar(select(DailyMustReadPush)) is None


@pytest.mark.integration
def test_unfollowing_segment_after_expansion_prevents_browser_delivery(generic_data, monkeypatch):
    data = generic_data
    identifier = publish(data, PushAudience.segment(source_ids=[data.sources[0]]))
    assert expand_web_push_events(data.factory, now=NOW) == 3
    job = next(job for job in deliveries(data, identifier) if job.user_id == data.users[2])
    with data.factory.begin() as session:
        session.execute(delete(UserSource).where(UserSource.user_id == data.users[2]))
    sent = Mock(side_effect=AssertionError("an unfollowed user must not receive this notification"))
    monkeypatch.setattr(web_push, "send_request", sent)
    web_push.deliver_web_push(str(job.id))
    sent.assert_not_called()
    with data.factory() as session:
        finished = session.get(WebPushDelivery, job.id)
        assert finished.status == "succeeded" and finished.accepted_at is None
        assert "no longer eligible" in finished.error


@pytest.mark.integration
@pytest.mark.parametrize("change", ["kind", "owner", "consent", "session"])
def test_generic_sender_rechecks_kind_account_and_enrollment_identity(
    generic_data, monkeypatch, change
):
    data = generic_data
    identifier = publish(data, PushAudience.users(data.users[0]))
    expand_web_push_events(data.factory, now=NOW)
    job = deliveries(data, identifier)[0]
    with data.factory.begin() as session:
        subscription = session.get(WebPushSubscription, job.subscription_id)
        if change == "kind":
            subscription.allowed_kinds = ["daily_must_read"]
        elif change == "owner":
            subscription.user_id = data.users[1]
        elif change == "consent":
            subscription.consent_id = uuid.uuid4()
        else:
            subscription.session_hash = "another-session"
    sent = Mock(side_effect=AssertionError("revoked consent must not reach a relay"))
    monkeypatch.setattr(web_push, "send_request", sent)
    web_push.deliver_web_push(str(job.id))
    sent.assert_not_called()
    with data.factory() as session:
        finished = session.get(WebPushDelivery, job.id)
        assert finished.status == "succeeded" and finished.accepted_at is None
