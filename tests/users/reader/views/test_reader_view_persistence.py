"""Fresh authenticated devices restore committed reader layouts and intentional empty workspaces."""

import copy
import secrets
import time
import uuid
from contextlib import contextmanager

import pytest
from devfeed_core.models import UserAccount
from devfeed_user_api import auth
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient

from tests.support.api_fuzz_support import READER_ORIGIN, store_identity

pytestmark = pytest.mark.integration
VIEW = "/v1/user/settings/feed-view"
COLUMNS = "/v1/user/settings/columns?workspace=my_feed"


@contextmanager
def device(security, reader=0):
    """Each device owns an application, cookie jar, Redis session, and matching CSRF token."""
    identity = {
        **security.readers[reader]["identity"],
        "csrf_token": secrets.token_urlsafe(32),
        "renewed_at": int(time.time()),
    }
    settings = auth.get_settings()
    token = store_identity(security.store, auth, settings, identity)
    app = create_app()
    assert auth.require_user not in app.dependency_overrides
    with TestClient(
        app,
        base_url=READER_ORIGIN,
        cookies={auth.oidc.cookie_name(settings, "session"): token},
        headers={"Origin": READER_ORIGIN, "X-CSRF-Token": identity["csrf_token"]},
    ) as client:
        response = client.get("/v1/user/auth/me")
        assert response.status_code == 200
        assert response.json()["user_id"] == identity["user_id"]
        assert response.json()["csrf_token"] == identity["csrf_token"]
        yield client


def saved(security, reader=0):
    with security.database() as session:
        account = session.get(
            UserAccount, uuid.UUID(security.readers[reader]["identity"]["user_id"])
        )
        return copy.deepcopy(account.feed_settings)


def read(client, path, **kwargs):
    response = client.get(path, **kwargs)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_fresh_devices_restore_committed_layouts_and_ordered_columns_without_read_writes(
    api_security,
):
    security = api_security
    columns = [
        {"id": str(uuid.uuid4()), "feed": {"kind": "recommended"}},
        {"id": str(uuid.uuid4()), "feed": {"kind": "topic", "id": str(security.topic_id)}},
        {"id": str(uuid.uuid4()), "feed": {"kind": "source", "id": str(security.source_id)}},
    ]
    source_scope = f"source:{security.source_id}"
    layouts = {"my_feed": "columns", "latest": "compact", source_scope: "stacked"}
    with device(security) as first:
        first_csrf = first.headers["X-CSRF-Token"]
        first_cookie = first.cookies.get("__Host-devfeed_user_session")
        response = first.put(COLUMNS, json={"columns": columns, "mode": "replace"})
        assert response.status_code == 200
        assert response.json() == {"initialized": True, "columns": columns}
        for scope, view in layouts.items():
            response = first.put(VIEW, params={"feed": scope}, json={"view": view})
            assert response.status_code == 200
            assert response.json() == {"view": view}

    # The first client is closed before a separate SQLAlchemy session reads the committed JSON.
    committed = saved(security)
    assert committed["feed_views"] == layouts
    assert committed["feed_view_default"] == "cards"
    assert committed["my_feed_columns"] == columns
    assert committed["my_feed_columns_initialized"] is True

    with device(security) as second:
        assert second.headers["X-CSRF-Token"] != first_csrf
        assert second.cookies.get("__Host-devfeed_user_session") != first_cookie
        for scope, view in layouts.items():
            assert read(second, VIEW, params={"feed": scope}) == {"view": view}
        assert read(second, COLUMNS) == {"initialized": True, "columns": columns}
        assert saved(security) == committed

        # This is the real authentication dependency, including this device's own CSRF binding.
        forbidden = second.put(
            VIEW,
            params={"feed": "latest"},
            json={"view": "cards"},
            headers={"X-CSRF-Token": first_csrf},
        )
        assert forbidden.status_code == 403
        assert saved(security) == committed

        reordered = [columns[2], columns[0], columns[1]]
        response = second.put(COLUMNS, json={"columns": reordered, "mode": "replace"})
        assert response.status_code == 200
        assert response.json() == {"initialized": True, "columns": reordered}
        assert (
            second.put(VIEW, params={"feed": "latest"}, json={"view": "stacked"}).status_code == 200
        )

    with device(security) as reopened:
        assert read(reopened, COLUMNS) == {"initialized": True, "columns": reordered}
        assert read(reopened, VIEW, params={"feed": "latest"}) == {"view": "stacked"}
        assert read(reopened, VIEW, params={"feed": "my_feed"}) == {"view": "columns"}
        assert read(reopened, VIEW, params={"feed": source_scope}) == {"view": "stacked"}
    assert saved(security)["my_feed_columns"] == reordered

    with device(security, reader=1) as unrelated:
        for scope in layouts:
            assert read(unrelated, VIEW, params={"feed": scope}) == {"view": "cards"}
        assert read(unrelated, COLUMNS) == {"initialized": False, "columns": []}
    assert saved(security, reader=1) == {}


def test_a_fresh_device_preserves_deliberately_cleared_columns_and_does_not_seed_starters(
    api_security,
):
    security = api_security
    column = {"id": str(uuid.uuid4()), "feed": {"kind": "recommended"}}
    with device(security) as first:
        assert first.put(COLUMNS, json={"columns": [column]}).status_code == 200
        assert (
            first.put(VIEW, params={"feed": "my_feed"}, json={"view": "columns"}).status_code == 200
        )
        response = first.put(COLUMNS, json={"columns": [], "mode": "replace"})
        assert response.status_code == 200
        assert response.json() == {"initialized": True, "columns": []}

    committed = saved(security)
    assert committed["my_feed_columns"] == []
    assert committed["my_feed_columns_initialized"] is True
    assert committed["feed_views"]["my_feed"] == "columns"

    with device(security) as fresh:
        assert read(fresh, VIEW, params={"feed": "my_feed"}) == {"view": "columns"}
        assert read(fresh, COLUMNS) == {"initialized": True, "columns": []}
        assert saved(security) == committed

        # Even a stale first-use request must return the deliberate empty workspace unchanged.
        initialized = fresh.put(COLUMNS, json={"columns": [column], "mode": "initialize"})
        assert initialized.status_code == 200
        assert initialized.json() == {"initialized": True, "columns": []}
        assert saved(security) == committed

    with device(security) as reopened:
        assert read(reopened, COLUMNS) == {"initialized": True, "columns": []}
    assert saved(security) == committed
