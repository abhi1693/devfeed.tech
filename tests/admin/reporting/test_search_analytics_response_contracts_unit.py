"""Private analytics adapters bound provider reads and return typed aggregate documents."""

from unittest.mock import Mock, call

import pytest
from devfeed_admin_api import search_analytics as routes
from devfeed_core import config
from devfeed_core.search_engine import SearchUnavailable, Typesense
from pydantic import SecretStr

from tests.support.admin_editorial import operator_api
from tests.support.source_readiness import NOW, configuration


@pytest.fixture
def analytics(monkeypatch):
    settings = configuration(
        search_enabled=True,
        search_url="http://search:8108",
        search_admin_key=SecretStr("disposable-index-credential"),
        search_analytics_enabled=True,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "utcnow", lambda: NOW)
    engine = Mock(spec=Typesense)
    engine.prefix = "devfeed"
    engine.analytics_rules.return_value = [
        {"name": "z-popular"},
        None,
        {"name": 42},
        {},
        {"name": "a-nohits"},
    ]
    engine.analytics_queries.side_effect = [
        [{"query": "angular router", "count": 7}],
        [{"query": "new framework", "count": 0}],
    ]
    construct = Mock(return_value=engine)
    monkeypatch.setattr(routes, "Typesense", construct)
    with operator_api(routes) as api:
        api.settings, api.engine, api.construct = settings, engine, construct
        yield api


def test_disabled_analytics_returns_a_complete_empty_document_without_contacting_search(analytics):
    analytics.settings.search_analytics_enabled = False
    response = analytics.client.get("/v1/admin/search-analytics")
    assert response.status_code == 200
    assert response.json() == {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "enabled": False,
        "flush_interval_seconds": 0,
        "rules": [],
        "popular_queries": [],
        "nohits_queries": [],
    }
    analytics.construct.assert_not_called()


def test_enabled_analytics_preserves_provider_counts_and_filters_rule_names_in_a_bounded_read(
    analytics,
):
    response = analytics.client.get("/v1/admin/search-analytics", params={"limit": 12})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "enabled": True,
        "flush_interval_seconds": 60,
        "rules": ["a-nohits", "z-popular"],
        "popular_queries": [{"query": "angular router", "count": 7}],
        "nohits_queries": [{"query": "new framework", "count": 0}],
    }
    analytics.construct.assert_called_once_with(admin=True)
    assert analytics.engine.analytics_queries.call_args_list == [
        call("devfeed_search_queries", limit=12),
        call("devfeed_search_nohits", limit=12),
    ]
    analytics.session.execute.assert_not_called()
    analytics.session.commit.assert_not_called()


@pytest.mark.parametrize("limit", [0, 101])
def test_invalid_analytics_page_bound_is_rejected_before_provider_queries(analytics, limit):
    response = analytics.client.get("/v1/admin/search-analytics", params={"limit": limit})
    assert response.status_code == 422
    analytics.construct.assert_not_called()


@pytest.mark.parametrize("phase", ["rules", "popular", "nohits"])
def test_provider_outage_never_returns_partial_analytics_or_private_dependency_details(
    analytics, phase
):
    error = SearchUnavailable("private search endpoint and credential")
    if phase == "rules":
        analytics.engine.analytics_rules.side_effect = error
    else:
        analytics.engine.analytics_queries.side_effect = (
            [error] if phase == "popular" else [[{"query": "angular router", "count": 7}], error]
        )
    response = analytics.client.get("/v1/admin/search-analytics")
    assert response.status_code == 503
    assert response.json() == {"detail": "Search analytics is temporarily unavailable"}
    assert "private" not in response.text and "angular router" not in response.text
    analytics.session.commit.assert_not_called()
