"""Ranking policy over eligible candidate rows, independent of SQL eligibility."""

import uuid
from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest
from devfeed_core.recommendations import MAX_RECOMMENDATIONS, ranked_candidates

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
USER = uuid.UUID(int=1000)


class EligibleCandidates:
    """Supply the read boundary; deliberately do not emulate database query behavior."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_):
        return self

    def mappings(self):
        return iter(self.rows)


def candidate(identifier, *, signal=1, reason="followed_topic", weight=100, age=timedelta(0)):
    matched = uuid.UUID(int=signal)
    seed = uuid.UUID(int=signal + 10_000) if reason == "related_topic" else matched
    return {
        "id": uuid.UUID(int=identifier),
        "topic_id": matched if reason not in {"followed_source", "followed_tag"} else None,
        "seed_topic_id": seed if reason not in {"followed_source", "followed_tag"} else None,
        "source_id": matched if reason == "followed_source" else None,
        "tag_id": matched if reason == "followed_tag" else None,
        "reason": reason,
        "weight": weight,
        "feed_at": NOW - age,
    }


def rank(rows, *, interest_count=3):
    return ranked_candidates(
        EligibleCandidates(rows), USER, NOW, interest_count, ["article"], ["en"]
    )


def test_empty_eligible_candidates_produce_an_empty_ranking():
    assert rank([]) == []


def test_freshness_decays_without_overriding_a_stronger_explicit_follow():
    rows = [
        candidate(10, signal=1),
        candidate(20, signal=2, age=timedelta(days=14)),
        candidate(30, signal=3, reason="related_topic", weight=40),
    ]

    result = rank(rows)

    assert [row["article_id"] for row in result] == [uuid.UUID(int=value) for value in [10, 20, 30]]
    assert result[0]["score"] > result[1]["score"] > result[2]["score"]
    assert result[0]["score"] <= 140


def test_future_timestamp_does_not_grant_more_than_the_freshness_bonus():
    result = rank(
        [
            candidate(10, signal=1, weight=60, reason="liked_topic"),
            candidate(20, signal=2, weight=60, reason="liked_topic", age=-timedelta(hours=1)),
        ]
    )

    assert {row["score"] for row in result} == {100}


def test_overlapping_topic_and_source_matches_keep_one_strongest_reason():
    result = rank(
        [
            candidate(10, signal=1, reason="liked_topic", weight=60),
            candidate(10, signal=2, reason="followed_source"),
            candidate(20, signal=3),
        ]
    )

    assert len(result) == 2
    matched = next(row for row in result if row["article_id"] == uuid.UUID(int=10))
    assert matched["reason"] == "followed_source"
    assert matched["source_id"] == uuid.UUID(int=2)
    assert matched["topic_id"] is None
    assert matched["score"] == 140


def test_equal_strength_duplicate_signals_choose_the_same_reason_for_every_input_order():
    rows = [
        candidate(10, signal=1),
        candidate(10, signal=2, reason="followed_source"),
        candidate(10, signal=3, reason="followed_tag"),
    ]

    results = [rank(list(order)) for order in permutations(rows)]

    assert all(result == results[0] for result in results)
    assert len(results[0]) == 1


def test_diversity_gives_another_interest_a_turn_before_repeating_the_winning_interest():
    rows = [
        candidate(10, signal=1),
        candidate(20, signal=1),
        candidate(30, signal=1),
        candidate(40, signal=2, age=timedelta(hours=6)),
    ]

    result = rank(rows)

    assert result[0]["topic_id"] == uuid.UUID(int=1)
    assert result[1]["article_id"] == uuid.UUID(int=40)
    assert result[1]["topic_id"] == uuid.UUID(int=2)
    assert [row["position"] for row in result] == [1, 2, 3, 4]
    assert result[2]["score"] < result[0]["score"]


@pytest.mark.parametrize("reason", ["followed_topic", "followed_source", "followed_tag"])
def test_ranking_retains_the_owning_user_and_winning_signal(reason):
    result = rank([candidate(10, signal=7, reason=reason)])

    assert result[0]["user_id"] == USER
    assert result[0]["reason"] == reason
    assert result[0]["article_id"] == uuid.UUID(int=10)
    assert result[0]["position"] == 1
    assert {
        key for key in result[0] if key.endswith("_id") and result[0][key] == uuid.UUID(int=7)
    } == (
        {"source_id"}
        if reason == "followed_source"
        else {"tag_id"}
        if reason == "followed_tag"
        else {"topic_id", "seed_topic_id"}
    )


def test_rankings_are_bounded_unique_and_stable_when_candidate_order_changes():
    # Each interest supplies 300 rows, within its 500-row retrieval limit.
    rows = [
        candidate(identifier, signal=1 if identifier <= 300 else 2) for identifier in range(1, 601)
    ]

    result = rank(rows, interest_count=2)

    assert len(result) == MAX_RECOMMENDATIONS == 500
    assert len({row["article_id"] for row in result}) == 500
    assert [row["position"] for row in result] == list(range(1, 501))
    assert min(row["score"] for row in result) > 0  # Repetition penalties remain bounded.
    assert rank(list(reversed(rows)), interest_count=2) == result
