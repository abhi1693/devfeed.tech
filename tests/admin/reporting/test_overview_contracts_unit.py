"""Chart HTTP boundaries reject unsupported and invalid date protocols."""

import pytest
from fastapi import HTTPException


@pytest.mark.parametrize(
    "query,valid",
    [
        (b"", True),
        (b"from=now-2y&to=now", True),
        (b"from=2024-11-01&to=2024-11-16", True),
        (b"range=P30D", False),
        (b"days=7", False),
        (b"from=2024-11-01&to=2024-11-16&start=2024-11-01", False),
    ],
)
def test_saved_http_reads_accept_only_the_from_to_protocol(query, valid):
    from devfeed_admin_api.overview import validate_report_query
    from starlette.requests import Request

    request = Request({"type": "http", "query_string": query})
    if valid:
        assert validate_report_query(request) is None
    else:
        with pytest.raises(HTTPException) as error:
            validate_report_query(request)
        assert error.value.status_code == 422


@pytest.mark.parametrize("lower,upper", [("bad", "now"), ("now", "now-1d"), ("now-3660d", "now")])
def test_invalid_calendar_windows_fail_before_saved_storage_is_opened(monkeypatch, lower, upper):
    import asyncio
    from unittest.mock import Mock

    from devfeed_admin_api import overview
    from starlette.responses import Response

    read = Mock(side_effect=AssertionError("Invalid dates reached analytics"))
    analytics = type("Analytics", (), {"section": read})()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            overview.overview_chart(
                "publication-rate",
                Response(),
                object(),
                analytics,
                from_value=lower,
                to_value=upper,
            )
        )
    assert error.value.status_code == 422
    read.assert_not_called()
