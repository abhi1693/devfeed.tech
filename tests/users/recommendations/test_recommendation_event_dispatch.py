"""Scheduler event dispatch must retain each fanout function's bounded contract."""

from devfeed_core import recommendations
from devfeed_core.models import RecommendationSourceEvent, UserSource


def test_topic_and_source_event_fanout_passes_exactly_six_arguments(monkeypatch):
    calls = []

    def expand(factory, event, event_id, interests, interest_id, batch):
        calls.append((factory, event, event_id, interests, interest_id, batch))
        return 1

    monkeypatch.setattr(recommendations, "_expand_events", expand)
    factory = object()
    assert recommendations.expand_recommendation_events(factory, batch=17) == 2
    assert len(calls) == 2
    assert calls[1] == (
        factory,
        RecommendationSourceEvent,
        RecommendationSourceEvent.source_id,
        UserSource,
        UserSource.source_id,
        17,
    )
