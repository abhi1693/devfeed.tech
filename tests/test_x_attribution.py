import json
from urllib.parse import quote

import pytest
from devfeed_user_api.x_attribution import click_id


def test_secure_cookie_precedes_pixel_cookie_and_http_cookie():
    cookies = {
        "__Host-devfeed_user_x_click": "landing-1",
        "devfeed_user_x_click": "development-2",
        "_twclid": quote(json.dumps({"twclid": "pixel-3"})),
    }
    assert click_id(cookies, secure=True) == "landing-1"
    assert click_id(cookies, secure=False) == "development-2"
    del cookies["__Host-devfeed_user_x_click"]
    assert click_id(cookies, secure=True) == "pixel-3"


@pytest.mark.parametrize(
    "raw", ["{", "null", "[]", '{"twclid":"bad;value"}', "x" * 4097, "[" * 1000]
)
def test_malformed_pixel_cookies_are_ignored(raw):
    assert click_id({"_twclid": raw}, secure=True) is None
