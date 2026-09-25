"""Canonical public handles. Account UUIDs remain the permanent identity."""

import re

RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "api",
        "auth",
        "devfeed",
        "help",
        "login",
        "logout",
        "me",
        "new",
        "profiles",
        "settings",
        "signup",
        "support",
        "system",
        "www",
    }
)
USERNAME_PATTERN = r"[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]"


def normalize_username(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().lower()
    if not re.fullmatch(USERNAME_PATTERN, value):
        raise ValueError(
            "Username must be 3-30 ASCII letters, digits, underscores or hyphens, "
            "starting and ending with a letter or digit"
        )
    if value in RESERVED_USERNAMES:
        raise ValueError("This username is reserved")
    return value
