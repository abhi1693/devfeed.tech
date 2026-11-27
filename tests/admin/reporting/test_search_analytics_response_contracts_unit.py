from devfeed_admin_api import search_analytics

from tests.support.admin_editorial import operator_api


def test_retired_search_analytics_does_not_initialise_a_search_provider():
    with operator_api(search_analytics) as api:
        response = api.client.get("/v1/admin/search-analytics")
        assert response.status_code == 410
