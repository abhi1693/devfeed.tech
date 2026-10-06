"""Explicit recipients, consent-aware pagination and current follower segments."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from devfeed_core.models import (
    Source,
    Topic,
    UserAccount,
    UserSource,
    UserTopic,
    WebPushSubscription,
)
from devfeed_core.push_audience import PushAudience, audience_contains, web_push_recipient_ids
from pydantic import ValidationError
from sqlalchemy import delete, insert, update

NOW = datetime(2026, 10, 6, 9, tzinfo=UTC)
CREATED = NOW - timedelta(hours=1)
BEFORE = CREATED - timedelta(days=1)
DAILY = "daily_must_read"


def test_audience_constructors_normalize_one_many_and_interest_segments():
    first, second = uuid.UUID(int=1), uuid.UUID(int=2)
    assert PushAudience.users(first).user_ids == (first,)
    assert PushAudience.users(second, first, second).user_ids == (first, second)
    assert PushAudience.all().model_dump(mode="json") == {
        "kind": "all",
        "user_ids": [],
        "topic_ids": [],
        "source_ids": [],
    }
    segment = PushAudience.segment(topic_ids=[second, first, second], source_ids=[second])
    assert segment.topic_ids == (first, second)
    assert segment.source_ids == (second,)
    assert PushAudience.model_validate(segment.model_dump(mode="json")) == segment


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "users"},
        {"kind": "users", "user_ids": [uuid.UUID(int=1)], "topic_ids": [uuid.UUID(int=2)]},
        {"kind": "all", "source_ids": [uuid.UUID(int=1)]},
        {"kind": "segment"},
        {"kind": "segment", "user_ids": [uuid.UUID(int=1)], "topic_ids": [uuid.UUID(int=2)]},
        {"kind": "all", "profile": {"email": "private@example.test"}},
        {"kind": "all", "sql": "SELECT * FROM user_accounts"},
        {"kind": "unknown"},
        {"kind": "users", "user_ids": ["invalid-account-id"]},
        {"kind": "users", "user_ids": [uuid.UUID(int=1)] * 1001},
        {"kind": "segment", "topic_ids": [uuid.UUID(int=1)] * 1001},
        {"kind": "segment", "source_ids": [uuid.UUID(int=1)] * 1001},
    ],
)
def test_audience_contract_rejects_ambiguous_unbounded_and_arbitrary_filters(payload):
    with pytest.raises(ValidationError):
        PushAudience.model_validate(payload)


@pytest.mark.parametrize("batch", [0, -1, 1001])
def test_recipient_pages_cannot_be_unbounded(batch):
    with pytest.raises(ValueError, match="between 1 and 1000"):
        web_push_recipient_ids(PushAudience.all(), DAILY, CREATED, NOW, batch=batch)


@pytest.fixture
def audience_data(database):
    users = [uuid.UUID(int=index) for index in range(1, 14)]
    topics = [uuid.uuid4() for _ in range(3)]
    sources = [uuid.uuid4() for _ in range(2)]
    with database.begin() as session:
        session.execute(
            insert(UserAccount),
            [
                dict(
                    id=user_id,
                    issuer="https://id.test",
                    subject=str(user_id),
                    organization_id="org",
                    created_at=CREATED + timedelta(seconds=1) if index == 9 else BEFORE,
                )
                for index, user_id in enumerate(users, start=1)
            ],
        )
        session.execute(
            insert(Topic),
            [
                dict(
                    id=topic_id,
                    name=f"Interest {index}",
                    slug=f"interest-{index}",
                    kind="technology",
                    status="proposed" if index == 2 else "active",
                )
                for index, topic_id in enumerate(topics)
            ],
        )
        session.execute(
            insert(Source),
            [
                dict(
                    id=source_id,
                    name=f"Publisher {index}",
                    feed_url=f"https://publisher-{index}.test/feed",
                    source_type="publisher",
                    approval_status="pending" if index == 1 else "approved",
                )
                for index, source_id in enumerate(sources)
            ],
        )
        session.execute(
            insert(UserTopic),
            [
                dict(
                    user_id=users[user - 1],
                    topic_id=topics[topic],
                    created_at=CREATED + timedelta(seconds=1) if user == 11 else BEFORE,
                )
                for user, topic in [
                    (1, 0),
                    (2, 1),
                    (4, 2),
                    (6, 0),
                    (7, 0),
                    (8, 0),
                    (9, 0),
                    (10, 0),
                    (11, 0),
                    (13, 0),
                    (13, 1),
                ]
            ],
        )
        session.execute(
            insert(UserSource),
            [
                dict(user_id=users[user - 1], source_id=sources[source], created_at=BEFORE)
                for user, source in [(1, 0), (3, 0), (5, 1), (13, 0)]
            ],
        )
        subscriptions = []
        for index, user_id in enumerate(users, start=1):
            endpoint = f"https://fcm.googleapis.com/fcm/send/recipient-{index}"
            subscriptions.append(
                dict(
                    user_id=user_id,
                    endpoint=endpoint,
                    endpoint_hash=hashlib.sha256(endpoint.encode()).hexdigest(),
                    p256dh="opaque-test-key",
                    auth="opaque-test-auth",
                    timezone="UTC",
                    enabled=index != 6,
                    session_hash=hashlib.sha256(str(user_id).encode()).hexdigest(),
                    authorization_expires_at=NOW if index == 7 else NOW + timedelta(days=1),
                    created_at=CREATED + timedelta(seconds=1) if index == 10 else BEFORE,
                    allowed_kinds=["future_custom"] if index == 8 else [DAILY],
                )
            )
        # More browsers and more followed interests still mean one recipient.
        subscriptions.append(
            {
                **subscriptions[12],
                "endpoint": "https://fcm.googleapis.com/fcm/send/another-browser",
                "endpoint_hash": "b" * 64,
            }
        )
        session.execute(insert(WebPushSubscription), subscriptions)
    return NS(factory=database, users=users, topics=topics, sources=sources)


def recipients(data, audience, *, kind=DAILY, after=None, batch=100):
    with data.factory() as session:
        return list(
            session.scalars(
                web_push_recipient_ids(audience, kind, CREATED, NOW, after=after, batch=batch)
            )
        )


@pytest.mark.integration
def test_one_many_and_all_require_existing_current_kind_consent(audience_data):
    data = audience_data
    assert recipients(data, PushAudience.users(data.users[0])) == [data.users[0]]
    assert recipients(
        data, PushAudience.users(data.users[2], data.users[0], data.users[2], uuid.uuid4())
    ) == [data.users[0], data.users[2]]
    expected = [data.users[index - 1] for index in [1, 2, 3, 4, 5, 11, 12, 13]]
    assert recipients(data, PushAudience.all()) == expected
    assert recipients(data, PushAudience.all(), kind="future_custom") == [data.users[7]]
    assert recipients(data, PushAudience.all(), kind="unrecognized_kind") == []


@pytest.mark.integration
def test_recipient_keyset_pages_are_ordered_distinct_and_bounded(audience_data):
    data = audience_data
    audience = PushAudience.all()
    expected = recipients(data, audience)
    actual, cursor = [], None
    while page := recipients(data, audience, after=cursor, batch=2):
        assert len(page) <= 2
        actual.extend(page)
        cursor = page[-1]
    assert actual == expected
    assert len(set(actual)) == len(actual)


@pytest.mark.integration
def test_segments_union_followed_interests_and_exclude_late_or_inactive_membership(audience_data):
    data = audience_data
    segment = PushAudience.segment(topic_ids=data.topics, source_ids=data.sources)
    assert recipients(data, segment) == [data.users[index - 1] for index in [1, 2, 3, 13]]
    assert recipients(data, PushAudience.segment(topic_ids=[data.topics[0]])) == [
        data.users[index - 1] for index in [1, 13]
    ]
    assert recipients(data, PushAudience.segment(source_ids=[data.sources[0]])) == [
        data.users[index - 1] for index in [1, 3, 13]
    ]
    assert (
        recipients(
            data, PushAudience.segment(topic_ids=[data.topics[2]], source_ids=[data.sources[1]])
        )
        == []
    )


@pytest.mark.integration
def test_sender_membership_recheck_honors_unfollow_and_catalog_changes(audience_data):
    data = audience_data
    segment = PushAudience.segment(topic_ids=[data.topics[0]], source_ids=[data.sources[0]])
    with data.factory.begin() as session:
        assert audience_contains(session, segment, data.users[12], CREATED)
        assert not audience_contains(session, segment, data.users[10], CREATED)
        assert not audience_contains(session, PushAudience.all(), data.users[8], CREATED)
        assert audience_contains(session, PushAudience.users(data.users[0]), data.users[0], CREATED)
        assert not audience_contains(
            session, PushAudience.users(data.users[0]), data.users[2], CREATED
        )
        session.execute(delete(UserTopic).where(UserTopic.user_id == data.users[12]))
        # Remaining source membership keeps this account in the OR segment.
        assert audience_contains(session, segment, data.users[12], CREATED)
        session.execute(delete(UserSource).where(UserSource.user_id == data.users[12]))
        assert not audience_contains(session, segment, data.users[12], CREATED)
        session.execute(update(Topic).where(Topic.id == data.topics[0]).values(status="proposed"))
        session.execute(
            update(Source).where(Source.id == data.sources[0]).values(approval_status="rejected")
        )
        assert not audience_contains(session, segment, data.users[0], CREATED)
