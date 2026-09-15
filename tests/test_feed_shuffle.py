"""Permutation properties independent of database rank gaps."""

import uuid
from collections import Counter
from types import SimpleNamespace

from devfeed_core.feed_generations import shuffled_ids


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
