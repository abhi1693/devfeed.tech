"""Partner integration tests use isolated direct analytical stores."""

import pytest

from tests.support.partner_analytics import partner_analytics as partner_analytics


@pytest.fixture(autouse=True)
def direct_partner_metrics(request):
    if request.node.get_closest_marker("integration") is not None:
        return request.getfixturevalue("partner_analytics")
