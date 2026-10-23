"""Independent feed layout choices preserve account ownership and concurrent changes."""

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from devfeed_core.models import UserAccount

from tests.support.user_personalization import user_data as user_data

pytestmark = pytest.mark.integration
PATH = "/v1/user/settings/feed-view"
LEGACY = "/v1/user/settings/feed"


def store(database, account, settings):
    with database.begin() as session:
        session.get(UserAccount, account).feed_settings = copy.deepcopy(settings)


def stored(database, account):
    with database() as session:
        return copy.deepcopy(session.get(UserAccount, account).feed_settings)


def test_scoped_layouts_are_independent_and_legacy_saves_preserve_the_frozen_default(
    user_data, database
):
    client, _, account, other, _ = user_data
    initial = {
        "view": "stacked",
        "languages": ["fr"],
        "content_types": ["news"],
        "my_feed_columns": [],
        "my_feed_columns_initialized": True,
        "unknown": {"preserve": True},
    }
    store(database, account, initial)
    assert client.get(PATH, params={"feed": "my_feed"}).json() == {"view": "stacked"}
    assert stored(database, account) == initial
    for scope, view in [("my_feed", "columns"), ("latest", "compact"), ("trending", "cards")]:
        response = client.put(PATH, params={"feed": scope}, json={"view": view})
        assert response.status_code == 200 and response.json() == {"view": view}
        assert response.headers["cache-control"] == "no-store"
    assert stored(database, account) == {
        **initial,
        "feed_view_default": "stacked",
        "feed_views": {"my_feed": "columns", "latest": "compact", "trending": "cards"},
    }
    assert stored(database, other) == {}
    response = client.put(
        LEGACY, json={"view": "compact", "languages": ["fr"], "content_types": ["news"]}
    )
    assert response.status_code == 200
    assert set(response.json()) == {"view", "columns", "languages", "content_types"}
    for scope, view in [
        ("my_feed", "columns"),
        ("latest", "compact"),
        ("trending", "cards"),
        ("search", "stacked"),
    ]:
        response = client.get(PATH, params={"feed": scope})
        assert response.status_code == 200 and response.json() == {"view": view}
        assert response.headers["cache-control"] == "no-store"
    assert stored(database, account)["unknown"] == initial["unknown"]
    assert stored(database, account)["my_feed_columns_initialized"] is True


def test_semantic_entity_scopes_canonicalize_without_borrowing_other_entity_layouts(
    user_data, database
):
    client, _, account, *_ = user_data
    identifier = uuid.uuid4()
    for kind, view in [("topic", "compact"), ("source", "stacked"), ("tag", "cards")]:
        response = client.put(
            PATH, params={"feed": f"{kind}:{identifier.hex.upper()}"}, json={"view": view}
        )
        assert response.status_code == 200 and response.json() == {"view": view}
        assert client.get(PATH, params={"feed": f"{kind}:{identifier}"}).json() == {"view": view}
    assert set(stored(database, account)["feed_views"]) == {
        f"{kind}:{identifier}" for kind in ("topic", "source", "tag")
    }
    for scope in [
        "read_later",
        "search",
        "content:article",
        "content:news",
        "content:tutorial",
        "content:release",
        "content:comparison",
        "content:opinion",
    ]:
        assert client.put(PATH, params={"feed": scope}, json={"view": "compact"}).status_code == 200
        assert client.get(PATH, params={"feed": scope}).json() == {"view": "compact"}


def test_invalid_scopes_bodies_and_columns_outside_my_feed_never_write_preferences(
    user_data, database
):
    client, _, account, *_ = user_data
    initial = {"view": "columns", "unknown": {"preserve": True}}
    store(database, account, initial)
    for feed in [
        None,
        "",
        "/latest",
        "future",
        "latest?cursor=secret",
        "content:video",
        "topic:slug",
        "x" * 65,
    ]:
        params = {} if feed is None else {"feed": feed}
        assert client.get(PATH, params=params).status_code == 422
        assert client.put(PATH, params=params, json={"view": "cards"}).status_code == 422
    for payload in [
        {},
        {"view": None},
        {"view": "future"},
        {"view": "cards", "user_id": "other"},
        {"view": "cards", "columns": []},
    ]:
        assert client.put(PATH, params={"feed": "my_feed"}, json=payload).status_code == 422
    for scope in [
        "latest",
        "trending",
        "search",
        "read_later",
        "content:article",
        f"topic:{uuid.uuid4()}",
    ]:
        assert client.put(PATH, params={"feed": scope}, json={"view": "columns"}).status_code == 422
        assert client.get(PATH, params={"feed": scope}).json() == {"view": "cards"}
    assert stored(database, account) == initial


@pytest.mark.parametrize("field", ["user_id", "subject", "issuer", "organization_id"])
def test_scoped_layouts_require_the_complete_account_identity(user_data, field):
    client, identity, _, other, _ = user_data
    setattr(identity, field, str(other) if field == "user_id" else "wrong-owner")
    assert client.get(PATH, params={"feed": "my_feed"}).status_code == 401
    assert client.put(PATH, params={"feed": "my_feed"}, json={"view": "columns"}).status_code == 401


def test_account_switches_keep_layouts_private(user_data):
    client, identity, first, second, _ = user_data
    assert client.put(PATH, params={"feed": "my_feed"}, json={"view": "columns"}).status_code == 200
    identity.user_id, identity.subject = str(second), "user-b"
    assert client.get(PATH, params={"feed": "my_feed"}).json() == {"view": "cards"}
    assert client.put(PATH, params={"feed": "latest"}, json={"view": "stacked"}).status_code == 200
    identity.user_id, identity.subject = str(first), "user-a"
    assert client.get(PATH, params={"feed": "my_feed"}).json() == {"view": "columns"}
    assert client.get(PATH, params={"feed": "latest"}).json() == {"view": "cards"}


def test_capacity_limit_rejects_only_new_overrides_and_leaves_storage_intact(user_data, database):
    client, _, account, *_ = user_data
    views = {f"source:{uuid.UUID(int=index)}": "cards" for index in range(256)}
    initial = {"feed_views": views, "feed_view_default": "compact", "unknown": "preserve"}
    store(database, account, initial)
    response = client.put(PATH, params={"feed": "latest"}, json={"view": "stacked"})
    assert response.status_code == 422
    assert stored(database, account) == initial
    scope = next(iter(views))
    assert client.put(PATH, params={"feed": scope}, json={"view": "stacked"}).status_code == 200
    assert len(stored(database, account)["feed_views"]) == 256
    assert client.get(PATH, params={"feed": scope}).json() == {"view": "stacked"}


def test_malformed_stored_layouts_recover_without_get_writes(user_data, database):
    client, _, account, *_ = user_data
    for value, view in [
        (None, "compact"),
        ([], "compact"),
        ({"latest": "future"}, "compact"),
        ({"latest": "columns"}, "cards"),
        ({"latest": "stacked"}, "stacked"),
    ]:
        initial = {
            "view": "compact",
            "feed_views": value,
            "feed_view_default": "future",
            "unknown": "preserve",
        }
        store(database, account, initial)
        response = client.get(PATH, params={"feed": "latest"})
        assert response.status_code == 200 and response.json() == {"view": view}
        assert stored(database, account) == initial


def test_legacy_save_racing_two_scope_updates_preserves_every_change(user_data, database):
    client, _, account, *_ = user_data
    initial = {
        "view": "stacked",
        "unknown": {"preserve": True},
        "my_feed_columns": [],
        "my_feed_columns_initialized": True,
    }
    store(database, account, initial)
    barrier = Barrier(3)

    def save(candidate):
        scope, view = candidate
        barrier.wait(timeout=5)
        return (
            client.put(PATH, params={"feed": scope}, json={"view": view})
            if scope
            else client.put(LEGACY, json={"view": view})
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(save, [("my_feed", "columns"), ("latest", "cards"), (None, "compact")])
        )
    assert all(response.status_code == 200 for response in responses)
    result = stored(database, account)
    assert result["feed_views"] == {"my_feed": "columns", "latest": "cards"}
    assert result["view"] == "compact"
    assert result["feed_view_default"] in {"stacked", "compact"}
    assert result["unknown"] == initial["unknown"]
    assert result["my_feed_columns"] == [] and result["my_feed_columns_initialized"] is True
