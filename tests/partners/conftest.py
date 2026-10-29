"""Partner integration tests use isolated direct analytical stores."""

import pytest

from tests.support.partner_analytics import partner_analytics as partner_analytics


@pytest.fixture
def priority_queue_routes(monkeypatch):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "job_queue_overrides", {})


@pytest.fixture
def publication_capacity_budget(monkeypatch):
    # Capacity contracts must not depend on runner speed. Dedicated clock-driven
    # publisher tests retain the production one-second deadline assertions.
    from devfeed_aggregator import partner_outbox, partner_sync_tasks

    monkeypatch.setattr(partner_outbox, "PUBLICATION_BATCH_SECONDS", 60.0)
    monkeypatch.setattr(partner_sync_tasks, "PUBLICATION_BATCH_SECONDS", 60.0)


@pytest.fixture(autouse=True)
def direct_partner_metrics(request):
    if request.node.get_closest_marker("integration") is not None:
        return request.getfixturevalue("partner_analytics")
