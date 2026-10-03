"""Bounded click identifiers from our first-party cookie or the X pixel cookie."""

import json
import re
from collections.abc import Mapping
from urllib.parse import unquote


def valid_click_id(value: object) -> str | None:
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value) else None
    )


def click_id(cookies: Mapping[str, str], *, secure: bool) -> str | None:
    name = "__Host-devfeed_user_x_click" if secure else "devfeed_user_x_click"
    captured = valid_click_id(cookies.get(name))
    if captured:
        return captured
    raw = cookies.get("_twclid", "")
    if not raw or len(raw) > 4096:
        return None
    try:
        value = json.loads(unquote(raw))
    except (ValueError, RecursionError):
        return None
    return valid_click_id(value.get("twclid")) if isinstance(value, dict) else None
