"""Load only this consumer's provisioned credentials, then replace this process."""

import json
import os
import sys
from pathlib import Path

ORIGIN = "http://chimely:8080"
COMMON = {
    "DEVFEED_CHIMELY_API_URL",
    "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT",
    "DEVFEED_CHIMELY_USER_ENVIRONMENT",
}
FIELDS = {
    "worker": COMMON | {"DEVFEED_CHIMELY_ADMIN_API_KEY", "DEVFEED_CHIMELY_USER_API_KEY"},
    "admin": COMMON | {"DEVFEED_CHIMELY_ADMIN_HMAC_SECRET"},
    "user": COMMON | {"DEVFEED_CHIMELY_USER_HMAC_SECRET"},
}


def credentials(role, env, path=Path("/run/devfeed-chimely/credentials.json")):
    if role not in FIELDS:
        raise ValueError("Unknown Chimely consumer")
    if env.get("DEVFEED_NOTIFICATIONS_ENABLED", "false").lower() not in {"true", "1", "yes", "on"}:
        return {}
    if env.get("DEVFEED_CHIMELY_API_URL", ORIGIN).rstrip("/") != ORIGIN:
        return {}
    values = json.loads(path.read_text())
    if not isinstance(values, dict) or set(values) - FIELDS[role]:
        raise ValueError("Invalid Chimely credential scope")
    if not all(isinstance(value, str) for value in values.values()):
        raise ValueError("Invalid Chimely credentials")
    expected = {
        "DEVFEED_CHIMELY_API_URL": ORIGIN,
        "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": env.get("DEVFEED_CHIMELY_ADMIN_ENVIRONMENT")
        or "devfeed-admin",
        "DEVFEED_CHIMELY_USER_ENVIRONMENT": env.get("DEVFEED_CHIMELY_USER_ENVIRONMENT") or "",
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise ValueError("Chimely provisioning does not match this configuration")
    for audience in ("ADMIN", "USER") if role == "worker" else (role.upper(),):
        suffix = "API_KEY" if role == "worker" else "HMAC_SECRET"
        if expected[f"DEVFEED_CHIMELY_{audience}_ENVIRONMENT"] and not values.get(
            f"DEVFEED_CHIMELY_{audience}_{suffix}"
        ):
            raise ValueError("Chimely credentials are incomplete")
    return values


def consumer_environment(role, env, path=Path("/run/devfeed-chimely/credentials.json")):
    try:
        return {**env, **credentials(role, env, path)}
    except (OSError, ValueError):
        if role != "user":
            raise
        # Optional inbox availability must never block sign-in or personalization.
        print(
            "User inbox credentials unavailable; "
            "notifications disabled until reprovisioned and restarted",
            file=sys.stderr,
        )
        return {**env, "DEVFEED_NOTIFICATIONS_ENABLED": "false"}


if __name__ == "__main__":
    try:
        role, *command = sys.argv[1:]
        if not command:
            raise ValueError("Missing consumer command")
        environment = consumer_environment(role, os.environ)
    except Exception:
        print(
            "Chimely credentials are unavailable or stale; run docker compose up to provision them",
            file=sys.stderr,
        )
        sys.exit(1)
    os.execvpe(command[0], command, environment)
