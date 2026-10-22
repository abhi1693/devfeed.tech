"""Recommendation rebuilds honor explicit membership and never overwrite newer recovery state.

Read results are preselected repository facts. Statements record write intent;
these tests do not evaluate SQL eligibility, row locks, or transactional atomicity.
"""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import recommendations
from devfeed_core.models import RecommendationSourceEvent, RecommendationTopicEvent
from sqlalchemy.sql.dml import Delete, Insert
from test_runtime_recommendation_commands_unit import USER, Factory, state
from test_source_readiness_unit import NOW


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(recommendations, "utcnow", lambda: NOW)


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
    sessions = [Mock(), Mock(), Mock()]
    for session, row in zip(sessions, rows, strict=True):
        session.scalar.return_value = row
    queue = Mock()

    assert recommendations.dispatch_recommendations(Factory(*sessions), queue, batch=2) == 2

    assert [call.args[1] for call in queue.enqueue.call_args_list] == [
        str(row.user_id) for row in rows[:2]
    ]
    assert all(row.dispatched_at == NOW for row in rows[:2])
    assert rows[2].dispatched_at is None
    sessions[2].scalar.assert_not_called()
    assert len({call.kwargs["job_id"] for call in queue.enqueue.call_args_list}) == 2


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
