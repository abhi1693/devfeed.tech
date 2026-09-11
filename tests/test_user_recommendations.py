"""Real materialized graph lifecycle, serving, and durable background processing."""

import base64
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleTopic,
    RecommendationTopicEvent,
    TopicRelation,
    UserInterest,
    UserRecommendation,
    UserRecommendationState,
    UserTopic,
    utcnow,
)
from devfeed_core.recommendations import (
    dispatch_recommendations,
    expand_recommendation_events,
    refresh_recommendations,
)
from sqlalchemy import delete, func, insert, select, update
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


def drain_events(database):
    for _ in range(100):
        with database() as session:
            count = session.scalar(select(func.count()).select_from(RecommendationTopicEvent))
        if not count:
            return
        expand_recommendation_events(database, batch=1)
    raise AssertionError("Events did not drain")


def prepare(user_data, database):
    client, _, user, _, topics = user_data
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    drain_events(database)
    assert refresh_recommendations(database, user) == 110
    return client, user, topics


def test_follows_invalidate_immediately_and_bulk_sql_is_tracked(user_data, database):
    client, user, topics = prepare(user_data, database)
    assert client.get("/v1/user/feed").json()["status"] == "ready"
    # Direct SQL has the same invalidation behavior as API mutations.
    with database.begin() as session:
        session.execute(delete(UserTopic).where(UserTopic.user_id == user))
    result = client.get("/v1/user/feed").json()
    assert result["items"] == [] and result["status"] == "refreshing"
    assert refresh_recommendations(database, user) == 0
    result = client.get("/v1/user/feed").json()
    assert result["status"] == "ready" and result["has_interests"] is False
    with database() as session:
        assert not session.scalar(
            select(UserRecommendation.article_id).where(UserRecommendation.user_id == user)
        )


def test_approved_graph_hop_and_likes_generate_explainable_edges(user_data, database):
    client, user, topics = prepare(user_data, database)
    with database.begin() as session:
        session.execute(
            insert(TopicRelation).values(
                topic_id=topics[0], related_topic_id=topics[1], relation="related_to"
            )
        )
    drain_events(database)
    assert refresh_recommendations(database, user) == 111
    with database() as session:
        interest = session.get(UserInterest, (user, topics[1]))
        assert (interest.reason, interest.weight, interest.seed_topic_id) == (
            "related_topic",
            40,
            topics[0],
        )
    page = client.get("/v1/user/feed?limit=100").json()
    assert page["reasons"] and all(r["kind"] == "followed_topic" for r in page["reasons"].values())
    # Removing the saved edge schedules recomputation and removes inferred candidates.
    with database.begin() as session:
        session.execute(delete(TopicRelation))
    drain_events(database)
    assert refresh_recommendations(database, user) == 110
    client.put("/v1/user/preferences", json={"topic_ids": []})
    with database.begin() as session:
        article = session.scalar(select(Article.id).where(Article.title == "Article 114"))
        session.execute(insert(ArticleLike).values(user_id=user, article_id=article))
    assert refresh_recommendations(database, user) == 111
    with database() as session:
        assert session.get(UserInterest, (user, topics[1])).reason == "liked_topic"
        assert session.get(UserInterest, (user, topics[0])) is None
    with database.begin() as session:
        session.execute(delete(ArticleLike).where(ArticleLike.user_id == user))
    assert client.get("/v1/user/feed").json()["status"] == "refreshing"
    assert refresh_recommendations(database, user) == 0


def test_withdrawals_are_filtered_before_background_refresh(user_data, database):
    client, user, topics = prepare(user_data, database)
    page = client.get("/v1/user/feed?limit=1").json()
    article = uuid.UUID(page["items"][0]["id"])
    with database.begin() as session:
        session.execute(
            update(Article).where(Article.id == article).values(publication_status="unpublished")
        )
    assert str(article) not in [
        r["id"] for r in client.get("/v1/user/feed?limit=100").json()["items"]
    ]
    drain_events(database)
    assert refresh_recommendations(database, user) == 109
    # Republishing an existing article also refreshes affected users.
    with database.begin() as session:
        session.execute(
            update(Article).where(Article.id == article).values(publication_status="published")
        )
    drain_events(database)
    assert refresh_recommendations(database, user) == 110


def test_refresh_generation_cursor_ownership_expiry_and_recovery(user_data, database):
    client, user, topics = prepare(user_data, database)
    page = client.get("/v1/user/feed?limit=1").json()
    cursor = page["next_cursor"]
    assert cursor
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id == user)
            .values(next_refresh_at=utcnow())
        )
    refresh_recommendations(database, user)
    assert client.get("/v1/user/feed", params={"cursor": cursor}).status_code == 409
    current = user_data[1]
    current.user_id, current.subject = str(user_data[3]), "user-b"
    assert client.get("/v1/user/feed", params={"cursor": cursor}).status_code == 422
    current.user_id, current.subject = str(user), "user-a"
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id == user)
            .values(expires_at=utcnow() - timedelta(seconds=1), next_refresh_at=utcnow())
        )
    assert client.get("/v1/user/feed").json()["status"] == "refreshing"
    refresh_recommendations(database, user)
    assert client.get("/v1/user/feed").json()["status"] == "ready"


def test_worker_failure_rolls_back_edges_and_retries(user_data, database, monkeypatch):
    client, user, topics = prepare(user_data, database)
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[1])]})
    from devfeed_core import recommendations

    original = recommendations.ranked_candidates

    def fail(*args):
        raise RuntimeError("simulated worker interruption")

    monkeypatch.setattr(recommendations, "ranked_candidates", fail)
    with pytest.raises(RuntimeError):
        refresh_recommendations(database, user)
    with database() as session:
        state = session.get(UserRecommendationState, user)
        assert state.attempts == 1 and state.invalidated
        assert state.next_refresh_at > utcnow() and state.dispatched_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserRecommendation)
                .where(UserRecommendation.user_id == user)
            )
            == 110
        )
    assert client.get("/v1/user/feed").json()["items"] == []
    monkeypatch.setattr(recommendations, "ranked_candidates", original)
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id == user)
            .values(next_refresh_at=utcnow())
        )
    assert refresh_recommendations(database, user) == 111


def test_topic_outbox_is_bounded_and_does_not_refresh_unrelated_users(user_data, database):
    client, user, topics = prepare(user_data, database)
    other = user_data[3]
    with database() as session:
        other_due = session.get(UserRecommendationState, other).next_refresh_at
    refresh_recommendations(database, other)
    with database.begin() as session:
        session.execute(insert(RecommendationTopicEvent).values(topic_id=topics[0]))
    assert expand_recommendation_events(database, batch=1) == 1
    with database() as session:
        assert session.get(UserRecommendationState, user).next_refresh_at <= utcnow()
        assert session.get(UserRecommendationState, other).next_refresh_at == other_due
        assert session.get(RecommendationTopicEvent, topics[0]).cursor == user
    # A new version never resets an in-progress page and starves users with larger IDs.
    with database.begin() as session:
        session.execute(update(RecommendationTopicEvent).values(version=2))
    expand_recommendation_events(database, batch=1)
    with database() as session:
        event = session.get(RecommendationTopicEvent, topics[0])
        assert event.cursor is None and event.pass_version == 2
    drain_events(database)


def test_dispatch_is_durable_idempotent_and_recovers_lost_jobs(user_data, database):
    with database.begin() as session:
        session.execute(
            insert(UserTopic),
            [
                dict(user_id=user_id, topic_id=user_data[4][0])
                for user_id in (user_data[2], user_data[3])
            ],
        )

    class Queue:
        def __init__(self):
            self.calls = []

        def enqueue(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(id="rq")

    queue = Queue()
    assert dispatch_recommendations(database, queue) == 2
    assert dispatch_recommendations(database, queue) == 0

    for args, kwargs in queue.calls:
        assert args[0] == "devfeed_aggregator.recommendation_tasks.refresh"
        assert kwargs["job_timeout"] == 30
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState).values(dispatched_at=utcnow() - timedelta(minutes=10))
        )
    assert dispatch_recommendations(database, queue) == 2
    for _, _, user, other, _ in [user_data]:
        refresh_recommendations(database, user)
        refresh_recommendations(database, other)
    assert dispatch_recommendations(database, queue) == 0


def test_empty_users_skip_dispatch_and_computation_until_they_follow(
    user_data, database, monkeypatch
):
    from devfeed_core.recommendations import request_recommendation_refresh

    client, _, user_id, _, topics = user_data
    calls = []
    queue = SimpleNamespace(enqueue=lambda *args, **kwargs: calls.append(args))
    assert dispatch_recommendations(database, queue) == 0
    with database.begin() as session:
        assert request_recommendation_refresh(session, user_id) is False
    with monkeypatch.context() as patch:

        def unexpected(*args):
            raise AssertionError("Empty accounts must not compute interests")

        patch.setattr("devfeed_core.recommendations.interests", unexpected)
        assert refresh_recommendations(database, user_id) == 0
    with database() as session:
        state = session.get(UserRecommendationState, user_id)
        assert state.generation is None and state.computed_at is None
        assert state.dispatched_at is None and state.attempts == 0
    page = client.get("/v1/user/feed").json()
    assert page["status"] == "ready" and page["items"] == [] and not page["has_interests"]
    assert (
        client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]}).status_code == 200
    )
    assert dispatch_recommendations(database, queue) == 1
    assert len(calls) == 1 and calls[0][1] == str(user_id)
    assert refresh_recommendations(database, user_id) > 0


def test_admin_graph_exposes_only_opt_in_user_edges(user_data, database, admin_client):
    client, user, topics = prepare(user_data, database)
    base = "/v1/admin/knowledge/graph"
    response = admin_client.get(
        base, params={"focus": f"user:{user}", "layers": ["user", "article"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert any(node["kind"] == "user" and node["entity_id"] == str(user) for node in data["nodes"])
    assert {"follows", "recommended"} <= {edge["kind"] for edge in data["edges"]}
    assert (
        "same@example.test" not in response.text and "https://identity.example" not in response.text
    )
    assert not any(node["kind"] == "user" for node in admin_client.get(base).json()["nodes"])
    client.put("/v1/user/preferences", json={"topic_ids": []})
    refreshed = admin_client.get(
        base, params={"focus": f"user:{user}", "layers": ["user", "article"]}
    ).json()
    assert not any(edge["kind"] == "recommended" for edge in refreshed["edges"])


def test_changed_classification_is_hidden_without_waiting_for_refresh(user_data, database):
    client, user, topics = prepare(user_data, database)
    first = client.get("/v1/user/feed?limit=1").json()["items"][0]["id"]
    with database.begin() as session:
        session.execute(
            delete(ArticleTopic).where(
                ArticleTopic.article_id == uuid.UUID(first),
                ArticleTopic.topic_id == topics[0],
            )
        )
    assert first not in [
        item["id"] for item in client.get("/v1/user/feed?limit=100").json()["items"]
    ]


def test_graph_expansion_is_one_hop_and_never_promotes_pending_topics(user_data, database):
    from devfeed_core.models import Topic

    client, user, topics = prepare(user_data, database)
    with database.begin() as session:
        session.execute(
            insert(TopicRelation),
            [
                dict(
                    topic_id=topics[0],
                    related_topic_id=topics[1],
                    relation="depends_on",
                ),
                dict(
                    topic_id=topics[1],
                    related_topic_id=topics[2],
                    relation="depends_on",
                ),
            ],
        )
        session.execute(update(Topic).where(Topic.id == topics[2]).values(status="active"))
    drain_events(database)
    refresh_recommendations(database, user)
    with database() as session:
        assert session.get(UserInterest, (user, topics[1])) is not None
        assert session.get(UserInterest, (user, topics[2])) is None
    with database.begin() as session:
        session.execute(update(Topic).where(Topic.id == topics[1]).values(status="proposed"))
    drain_events(database)
    refresh_recommendations(database, user)
    with database() as session:
        assert session.get(UserInterest, (user, topics[1])) is None


def test_source_revocation_filters_prepared_articles_and_deleting_topic_invalidates(
    user_data, database
):
    from devfeed_core.models import Source, Topic

    client, user, topics = prepare(user_data, database)
    with database.begin() as session:
        session.execute(update(Source).values(approval_status="rejected"))
    assert client.get("/v1/user/feed").json()["items"] == []
    drain_events(database)
    assert refresh_recommendations(database, user) == 0
    with database.begin() as session:
        session.execute(delete(Topic).where(Topic.id == topics[0]))
    assert client.get("/v1/user/feed").json()["status"] == "refreshing"
    drain_events(database)
    refresh_recommendations(database, user)
    with database() as session:
        assert session.get(UserInterest, (user, topics[0])) is None


def test_unchanged_publication_updates_do_not_enqueue_recomputation(user_data, database):
    from devfeed_core.models import Source

    prepare(user_data, database)
    with database.begin() as session:
        session.execute(update(Article).values(publication_status=Article.publication_status))
        session.execute(update(Source).values(approval_status=Source.approval_status))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationTopicEvent)) == 0


@pytest.mark.parametrize(
    "owner,generation,position", [(1, "x", 1), ("self", 1, 1), ("self", "x", True)]
)
def test_malformed_cursor_values_are_rejected(user_data, owner, generation, position):
    client, _, user, _, _ = user_data
    cursor = base64.urlsafe_b64encode(
        json.dumps([str(user) if owner == "self" else owner, generation, position]).encode()
    ).decode()
    assert client.get("/v1/user/feed", params={"cursor": cursor}).status_code == 422
