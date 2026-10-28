"""Recommendation refresh, event fanout, dispatch, and recovery preserve reader state."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import recommendations as recommendations
from devfeed_core.cache import CacheUnavailable
from devfeed_core.delivery import delivery_id
from devfeed_core.models import (
    RecommendationSourceEvent,
    RecommendationTopicEvent,
    UserRecommendationState,
)
from sqlalchemy.sql.dml import Delete, Insert

from tests.support.source_readiness import NOW

USER = uuid.UUID(int=1000)


def state(**changes):
    return UserRecommendationState(
        **{
            "user_id": USER,
            "invalidated": True,
            "candidates_dirty": True,
            "next_refresh_at": NOW - timedelta(minutes=2),
            "dispatched_at": NOW - timedelta(minutes=1),
            "computed_at": None,
            "ranked_at": None,
            "expires_at": None,
            "preference_revision": 3,
            "attempts": 0,
            "interest_count": 0,
            "generation": None,
            **changes,
        }
    )


class Factory:
    def __init__(self, *sessions):
        self.sessions = iter(sessions)

    @contextmanager
    def begin(self):
        yield next(self.sessions)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(recommendations, "utcnow", lambda: NOW)


def test_explicit_refresh_leaves_an_account_without_any_recommendation_work_untouched():
    session = Mock()
    session.scalar.return_value = False
    assert not recommendations.request_recommendation_refresh(session, USER)
    session.execute.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("reason", ["already-pending", "clean", "failed", "future"])
def test_explicit_refresh_rearms_backoff_and_dirty_inputs_but_preserves_pending_dispatch(reason):
    current = state(candidates_dirty=False)
    if reason == "clean":
        current.invalidated = False
    elif reason == "failed":
        current.attempts = 3
    elif reason == "future":
        current.next_refresh_at = NOW + timedelta(hours=2)
    session = Mock()
    session.scalar.side_effect = [True, current]
    previous = current.dispatched_at
    assert recommendations.request_recommendation_refresh(session, USER)
    assert current.invalidated
    if reason == "already-pending":
        assert current.dispatched_at == previous and not current.candidates_dirty
    else:
        assert current.next_refresh_at == NOW and current.candidates_dirty
        assert current.dispatched_at is None and current.attempts == 0
    session.flush.assert_called_once()


@pytest.mark.parametrize("model", [RecommendationTopicEvent, RecommendationSourceEvent])
@pytest.mark.parametrize("page", ["full", "complete", "changed", "empty", "no-event"])
def test_catalog_fanout_rotates_pages_and_restarts_only_after_an_inflight_version_change(
    model, page
):
    field = "topic_id" if model is RecommendationTopicEvent else "source_id"
    event = model(
        **{field: uuid.uuid4()},
        cursor=uuid.UUID(int=1),
        version=3,
        pass_version=2 if page == "changed" else 3,
        created_at=NOW - timedelta(minutes=5),
    )
    ids = (
        [uuid.UUID(int=i) for i in range(2, 4 if page == "full" else 3)]
        if page not in {"empty", "no-event"}
        else []
    )
    states = [
        state(user_id=identifier, candidates_dirty=False, invalidated=False) for identifier in ids
    ]
    session = Mock()
    session.scalar.return_value = None if page == "no-event" else event
    session.scalars.side_effect = [ids, states] if ids else [ids]
    if model is RecommendationTopicEvent:
        membership, key = recommendations.UserInterest, recommendations.UserInterest.topic_id
    else:
        membership, key = recommendations.UserSource, recommendations.UserSource.source_id
    count = recommendations._expand_events(
        Factory(session), model, getattr(model, field), membership, key, 2
    )
    assert count == len(ids)
    assert all(item.candidates_dirty and not item.invalidated for item in states)
    if page == "changed":
        assert event.pass_version == 3 and event.cursor is None and event.created_at == NOW
        session.delete.assert_not_called()
    elif page in {"complete", "empty"}:
        session.delete.assert_called_once_with(event)
    else:
        session.delete.assert_not_called()
    if page == "full":
        assert event.cursor == ids[-1] and event.created_at == NOW
    if page == "no-event":
        session.scalars.assert_not_called()


def test_public_fanout_tick_processes_both_topics_and_sources(monkeypatch):
    expand = Mock(side_effect=[3, 4])
    monkeypatch.setattr(recommendations, "_expand_events", expand)
    factory = object()
    assert recommendations.expand_recommendation_events(factory, batch=17) == 7
    assert [call.args[1] for call in expand.call_args_list] == [
        RecommendationTopicEvent,
        RecommendationSourceEvent,
    ]
    assert all(call.args[-1] == 17 for call in expand.call_args_list)


@pytest.mark.parametrize("first_feed", [False, True])
def test_dispatch_uses_stable_due_delivery_identity_and_prioritizes_only_the_first_feed(first_feed):
    current = state(
        computed_at=None if first_feed else NOW - timedelta(hours=7), dispatched_at=None
    )
    session, queue = Mock(), Mock()
    session.scalars.return_value.all.return_value = [current]
    assert recommendations.dispatch_recommendations(Factory(session), queue, batch=3) == 1
    assert current.dispatched_at == NOW
    queue.enqueue.assert_called_once_with(
        "devfeed_aggregator.recommendation_tasks.refresh",
        str(USER),
        job_timeout=30,
        result_ttl=0,
        failure_ttl=86400,
        ttl=None,
        job_id=delivery_id("recommendations", USER, current.next_refresh_at),
        unique=True,
        at_front=first_feed,
    )


def test_dispatch_failure_does_not_mark_the_unsubmitted_delivery_as_dispatched():
    current = state(dispatched_at=None)
    session, queue = Mock(), Mock()
    session.scalars.return_value.all.return_value = [current]
    queue.enqueue.side_effect = RuntimeError("Redis disconnected")
    with pytest.raises(RuntimeError, match="Redis disconnected"):
        recommendations.dispatch_recommendations(Factory(session), queue)
    assert current.dispatched_at is None


def test_interest_expansion_keeps_explicit_follows_strongest_and_tracks_directed_seed_reasons():
    followed, liked, dependency, symmetric, retired = [uuid.UUID(int=i) for i in range(1, 6)]
    session = Mock()
    session.scalars.side_effect = [[followed], [followed, liked, dependency, symmetric]]
    session.execute.side_effect = [
        [(followed, 8), (liked, 1)],
        SimpleNamespace(
            all=lambda: [
                (followed, dependency, "depends_on"),
                (symmetric, liked, "related_to"),
                (followed, retired, "uses_language"),
            ]
        ),
    ]
    result = recommendations.interests(session, USER)
    assert result == [
        {
            "topic_id": followed,
            "seed_topic_id": followed,
            "weight": 100,
            "reason": "followed_topic",
        },
        {"topic_id": liked, "seed_topic_id": liked, "weight": 60, "reason": "liked_topic"},
        {
            "topic_id": dependency,
            "seed_topic_id": followed,
            "weight": 40,
            "reason": "related_topic",
        },
        {"topic_id": symmetric, "seed_topic_id": liked, "weight": 24, "reason": "related_topic"},
    ]
    session.add.assert_not_called()
    session.execute.assert_called()


@pytest.mark.parametrize("likes", [0, 9])
def test_interest_expansion_has_no_graph_read_without_seeds_and_caps_liked_weight(likes):
    identifier = uuid.uuid4()
    session = Mock()
    session.scalars.return_value = []
    session.execute.side_effect = (
        [[]] if not likes else [[(identifier, likes)], SimpleNamespace(all=lambda: [])]
    )
    result = recommendations.interests(session, USER)
    assert result == (
        []
        if not likes
        else [
            {
                "topic_id": identifier,
                "seed_topic_id": identifier,
                "weight": 80,
                "reason": "liked_topic",
            }
        ]
    )
    assert session.execute.call_count == (2 if likes else 1)


@pytest.mark.parametrize("cache_down", [False, True])
@pytest.mark.parametrize("rebuild", [False, True])
def test_refresh_rebuilds_only_dirty_or_old_candidates_and_preserves_pool_during_cache_outage(
    monkeypatch, rebuild, cache_down
):
    current = state(
        invalidated=rebuild,
        candidates_dirty=False,
        ranked_at=NOW - timedelta(hours=1),
        computed_at=NOW - timedelta(hours=1),
        generation=uuid.uuid4(),
        interest_count=2,
        attempts=2,
    )
    session = Mock()
    # State, durable input/work existence; source/tag counts and saved preferences only on rebuild.
    session.scalar.side_effect = [
        current,
        True,
        *([1, 0, {"content_types": ["tutorial"], "languages": ["en"]}] if rebuild else []),
        *([2] if cache_down else []),
    ]
    selected = [
        {
            "topic_id": uuid.uuid4(),
            "seed_topic_id": uuid.uuid4(),
            "weight": 40,
            "reason": "related_topic",
        }
    ]
    ranked = [
        {
            "user_id": USER,
            "article_id": uuid.uuid4(),
            "position": 1,
            "score": 100.0,
            "reason": "followed_source",
            "source_id": uuid.uuid4(),
            "topic_id": None,
            "seed_topic_id": None,
        }
    ]
    ranked.append({**ranked[0], "article_id": uuid.uuid4(), "position": 2})
    interest, rank = Mock(return_value=selected), Mock(return_value=ranked)
    publish = Mock(side_effect=CacheUnavailable if cache_down else None, return_value=2)
    monkeypatch.setattr(recommendations, "interests", interest)
    monkeypatch.setattr(recommendations, "ranked_candidates", rank)
    monkeypatch.setattr(recommendations, "publish_generation", publish)

    assert recommendations.refresh_recommendations(Factory(session), USER) == 2

    assert current.preference_revision == (4 if rebuild else 3)
    assert current.ranked_at == (NOW if rebuild else NOW - timedelta(hours=1))
    assert current.interest_count == 2 and not current.candidates_dirty and not current.invalidated
    assert current.computed_at == NOW and current.expires_at == NOW + timedelta(hours=24)
    assert current.next_refresh_at > NOW and current.dispatched_at is None and current.attempts == 0
    if rebuild:
        interest.assert_called_once_with(session, USER)
        rank.assert_called_once_with(session, USER, NOW, 2, ["tutorial"], ["en"])
        assert any(
            len(call.args) == 2 and call.args[1] == ranked
            for call in session.execute.call_args_list
        )
    else:
        interest.assert_not_called()
        rank.assert_not_called()
    publish.assert_called_once_with(session, current)
    if cache_down:
        assert current.generation is None


@pytest.mark.parametrize("stop", ["not-due", "no-inputs"])
def test_refresh_without_admitted_due_work_does_not_replace_any_candidates(monkeypatch, stop):
    session = Mock()
    session.scalar.side_effect = [None] if stop == "not-due" else [state(), False]
    publish = Mock()
    monkeypatch.setattr(recommendations, "publish_generation", publish)
    assert recommendations.refresh_recommendations(Factory(session), USER) == 0
    publish.assert_not_called()
    assert session.execute.call_count == 1  # The bounded statement timeout command only.


@pytest.mark.parametrize("attempts", [0, 4, 20])
def test_failed_refresh_records_bounded_backoff_without_exposing_database_error_to_state(
    monkeypatch, attempts
):
    failing, recovery = Mock(), Mock()
    failing.execute.side_effect = RuntimeError("private database connection detail")
    current = state(attempts=attempts)
    recovery.scalar.return_value = current
    with pytest.raises(RuntimeError, match="private database connection detail"):
        recommendations.refresh_recommendations(Factory(failing, recovery), USER)
    assert current.attempts == attempts + 1 and current.dispatched_at is None
    delay = (current.next_refresh_at - NOW).total_seconds()
    assert delay == (60 if attempts == 0 else 900)
    assert "error" not in current.__dict__


@pytest.mark.parametrize("model", [RecommendationTopicEvent, RecommendationSourceEvent])
def test_first_membership_fanout_page_marks_current_accounts_dirty_and_saves_its_cursor(model):
    field = "topic_id" if model is RecommendationTopicEvent else "source_id"
    event = model(
        **{field: uuid.uuid4()},
        cursor=None,
        version=1,
        pass_version=1,
        created_at=NOW - timedelta(minutes=5),
    )
    identifiers = [uuid.UUID(int=101), uuid.UUID(int=102)]
    states = [state(user_id=identifier, candidates_dirty=False) for identifier in identifiers]
    session = Mock()
    session.scalar.return_value = event
    session.scalars.side_effect = [identifiers, states]
    membership = (
        recommendations.UserInterest
        if model is RecommendationTopicEvent
        else recommendations.UserSource
    )

    count = recommendations._expand_events(
        Factory(session), model, getattr(model, field), membership, getattr(membership, field), 2
    )

    assert count == 2 and all(row.candidates_dirty for row in states)
    assert event.cursor == identifiers[-1] and event.created_at == NOW
    assert event.pass_version == event.version == 1
    session.delete.assert_not_called()


def test_dispatch_stops_after_its_batch_even_when_more_due_accounts_exist():
    rows = [state(user_id=uuid.UUID(int=value), dispatched_at=None) for value in (101, 102, 103)]
    session = Mock()
    session.scalars.return_value.all.return_value = rows[:2]
    queue = Mock()
    assert recommendations.dispatch_recommendations(Factory(session), queue, batch=2) == 2
    assert [call.args[1] for call in queue.enqueue.call_args_list] == [
        str(row.user_id) for row in rows[:2]
    ]
    assert all(row.dispatched_at == NOW for row in rows[:2])
    assert rows[2].dispatched_at is None
    session.scalars.assert_called_once()
    statement = session.scalars.call_args.args[0]
    assert statement.compile().params["param_1"] == 2
    assert "FOR UPDATE" in str(statement)
    assert len({call.kwargs["job_id"] for call in queue.enqueue.call_args_list}) == 2


def test_zero_recommendation_batch_opens_no_database_or_queue_resources():
    factory, queue = Mock(), Mock()
    assert recommendations.dispatch_recommendations(factory, queue, batch=0) == 0
    factory.begin.assert_not_called()
    queue.enqueue.assert_not_called()


def test_related_expansion_between_two_explicit_follows_cannot_downgrade_either_reason():
    first, second = uuid.UUID(int=101), uuid.UUID(int=102)
    session = Mock()
    session.scalars.side_effect = [[first, second], [first, second]]
    session.execute.side_effect = [
        [],
        SimpleNamespace(all=lambda: [(first, second, "related_to")]),
    ]

    result = recommendations.interests(session, USER)

    assert result == [
        {
            "topic_id": identifier,
            "seed_topic_id": identifier,
            "weight": 100,
            "reason": "followed_topic",
        }
        for identifier in (first, second)
    ]


@pytest.mark.parametrize("remaining_sources", [0, 1])
def test_rebuild_without_topic_interests_requests_stale_edge_cleanup_and_an_empty_generation(
    monkeypatch, remaining_sources
):
    previous_generation, next_generation = uuid.uuid4(), uuid.uuid4()
    current = state(
        generation=previous_generation,
        computed_at=NOW - timedelta(hours=7),
        ranked_at=NOW - timedelta(hours=7),
        expires_at=NOW + timedelta(hours=17),
        interest_count=1,
    )
    session = Mock()
    # Existing recommendations keep this account eligible after its final follow disappears.
    session.scalar.side_effect = [
        current,
        True,
        remaining_sources,
        0,
        {"content_types": ["tutorial"], "languages": ["en"]},
    ]
    monkeypatch.setattr(recommendations, "interests", Mock(return_value=[]))
    rank = Mock(return_value=[])
    monkeypatch.setattr(recommendations, "ranked_candidates", rank)

    def publish(session_arg, state_arg):
        assert session_arg is session and state_arg is current
        current.generation = next_generation
        return 0

    monkeypatch.setattr(recommendations, "publish_generation", publish)

    assert recommendations.refresh_recommendations(Factory(session), USER) == 0

    writes = [call.args[0] for call in session.execute.call_args_list]
    assert {statement.table.name for statement in writes if isinstance(statement, Delete)} == {
        "user_interests",
        "user_recommendations",
    }
    assert not any(isinstance(statement, Insert) for statement in writes)
    assert current.generation == next_generation and current.preference_revision == 4
    assert current.interest_count == remaining_sources
    assert not current.invalidated and not current.candidates_dirty
    assert current.computed_at == current.ranked_at == NOW
    assert current.next_refresh_at == NOW + timedelta(hours=6)
    if remaining_sources:
        rank.assert_called_once_with(session, USER, NOW, 1, ["tutorial"], ["en"])
    else:
        rank.assert_not_called()


@pytest.mark.parametrize("deleted", [False, True])
def test_failure_recovery_cannot_reschedule_a_deleted_account_or_a_newer_completed_refresh(deleted):
    failing, recovery = Mock(), Mock()
    failure = RuntimeError("database unavailable")
    failing.execute.side_effect = failure
    newer = state(
        invalidated=False,
        candidates_dirty=False,
        computed_at=NOW - timedelta(hours=1),
        ranked_at=NOW - timedelta(hours=1),
        next_refresh_at=NOW + timedelta(hours=5),
        dispatched_at=None,
        attempts=0,
    )
    recovery.scalar.return_value = None if deleted else newer
    before = copy.deepcopy((newer.next_refresh_at, newer.dispatched_at, newer.attempts))

    with pytest.raises(RuntimeError) as raised:
        recommendations.refresh_recommendations(Factory(failing, recovery), USER)

    assert raised.value is failure
    assert (newer.next_refresh_at, newer.dispatched_at, newer.attempts) == before
    recovery.execute.assert_not_called()
    recovery.add.assert_not_called()
