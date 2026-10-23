"""Personal feed generations preserve immutable sequence publication and stable shuffling."""

import uuid
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import feed_generations as feeds
from devfeed_core.cache import CacheUnavailable
from devfeed_core.feed_generations import shuffled_ids
from devfeed_core.models import UserRecommendation, UserRecommendationState

from tests.support.source_readiness import NOW


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("cache_failure", [False, True])
def test_sequence_is_written_before_exposing_the_new_generation_and_failure_retains_the_old_one(
    monkeypatch, empty, cache_failure
):
    user, generation, previous, publisher = (uuid.uuid4() for _ in range(4))
    state = UserRecommendationState(
        user_id=user,
        preference_revision=7,
        generation=previous,
        computed_at=NOW,
        ranked_at=NOW,
        invalidated=False,
    )
    candidates = (
        []
        if empty
        else [
            UserRecommendation(
                user_id=user,
                article_id=uuid.UUID(int=index),
                position=index,
                score=100 - index,
                reason="followed_source",
                source_id=publisher,
            )
            for index in range(1, 4)
        ]
    )
    session, cache = Mock(), Mock()
    session.scalars.return_value = candidates
    session.execute.return_value.all.return_value = [
        (row.article_id, publisher) for row in candidates
    ]
    monkeypatch.setattr(feeds, "get_cache", lambda: cache)
    monkeypatch.setattr(feeds, "uuid", SimpleNamespace(uuid4=lambda: generation, UUID=uuid.UUID))

    def write(key, values, ttl):
        assert state.generation == previous
        assert key == f"v1:{user}:7:{generation}"
        assert ttl == 9 * 3600
        if empty:
            assert values == [b""]
        else:
            assert len(values) == len(candidates) and set(values) == {
                row.article_id.bytes for row in candidates
            }
        if cache_failure:
            raise CacheUnavailable

    cache.write_sequence.side_effect = write
    if cache_failure:
        with pytest.raises(CacheUnavailable):
            feeds.publish_generation(session, state)
        assert state.generation == previous
    else:
        assert feeds.publish_generation(session, state) == len(candidates)
        assert state.generation == generation
    cache.write_sequence.assert_called_once()
    if empty:
        session.execute.assert_not_called()
    session.commit.assert_not_called()
    assert state.preference_revision == 7 and state.ranked_at == NOW


@pytest.mark.parametrize("stored", ["missing", "empty", "ordered", "corrupt", "mixed-marker"])
def test_cached_generation_keeps_owner_revision_and_slice_order_and_rejects_corrupt_ids(
    monkeypatch, stored
):
    user, generation = uuid.uuid4(), uuid.uuid4()
    identifiers = [uuid.UUID(int=index) for index in (8, 3)]
    rows = {
        "missing": None,
        "empty": [b""],
        "ordered": [value.bytes for value in identifiers],
        "corrupt": [b"too short"],
        "mixed-marker": [identifiers[0].bytes, b""],
    }[stored]
    cache = Mock()
    cache.read_sequence.return_value = rows
    monkeypatch.setattr(feeds, "get_cache", lambda: cache)
    if stored in {"corrupt", "mixed-marker"}:
        with pytest.raises(CacheUnavailable):
            feeds.generation_ids(user, 7, generation, 12, 2)
    else:
        assert (
            feeds.generation_ids(user, 7, generation, 12, 2)
            == {"missing": None, "empty": [], "ordered": identifiers}[stored]
        )
    cache.read_sequence.assert_called_once_with(f"v1:{user}:7:{generation}", 12, 13)
    cache.write_sequence.assert_not_called()


def test_shuffle_is_reproducible_varied_complete_and_diverse():
    candidates = [
        SimpleNamespace(article_id=uuid.UUID(int=i + 1), position=i * 10 + 1) for i in range(500)
    ]
    publishers = {row.article_id: index // 100 for index, row in enumerate(candidates)}
    first = shuffled_ids(candidates, uuid.UUID(int=1), publishers)
    assert first == shuffled_ids(candidates, uuid.UUID(int=1), publishers)
    assert first != shuffled_ids(candidates, uuid.UUID(int=2), publishers)
    assert len(first) == len(set(first)) == 500
    assert set(first) == set(publishers)
    assert max(Counter(publishers[identifier] for identifier in first[:8]).values()) <= 2
    assert shuffled_ids([], uuid.uuid4(), {}) == []
    assert shuffled_ids(candidates[:1], uuid.uuid4(), {}) == [candidates[0].article_id]
