"""Initialize saved workspaces atomically without changing the legacy feed receipt."""

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from devfeed_core.models import UserAccount, UserRecommendationState
from devfeed_core.user_settings import FeedSettings
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration
PATH = "/v1/user/settings/columns"
FEED_PATH = "/v1/user/settings/feed"
MY_PATH = f"{PATH}?workspace=my_feed"


def column(kind="latest", selection_id=None):
    feed = {"kind": kind}
    if kind in {"source", "topic", "tag"}:
        feed["id"] = selection_id or str(uuid.uuid4())
    return {"id": str(uuid.uuid4()), "feed": feed}


def store(database, account, value):
    with database.begin() as session:
        session.get(UserAccount, account).feed_settings = copy.deepcopy(value)


def stored(database, account):
    with database() as session:
        return copy.deepcopy(session.get(UserAccount, account).feed_settings)


@pytest.mark.parametrize("path", [PATH, f"{PATH}?workspace=latest", MY_PATH, FEED_PATH])
@pytest.mark.parametrize("kind", ["latest", "recommended", "source", "topic", "tag"])
def test_duplicate_feed_writes_are_rejected_without_changing_either_workspace(
    user_data, database, path, kind
):
    client, _, account, *_ = user_data
    initial = {
        "columns": [column()],
        "columns_initialized": True,
        "my_feed_columns": [column("recommended")],
        "my_feed_columns_initialized": True,
        "unknown": {"preserve": True},
    }
    store(database, account, initial)
    selection = uuid.uuid4()
    columns = [column(kind, str(selection)), column(kind, selection.hex.upper())]
    for mode in [None] if path == FEED_PATH else ["replace", "initialize", "restore_empty"]:
        payload = {"columns": columns}
        if mode is not None:
            payload["mode"] = mode
        response = client.put(path, json=payload)
        assert response.status_code == 422
        assert "Each feed can appear only once" in response.text
        assert stored(database, account) == initial


@pytest.mark.parametrize("path", [PATH, MY_PATH, FEED_PATH])
def test_same_selection_uuid_is_valid_for_different_feed_kinds(user_data, path):
    client = user_data[0]
    selection = str(uuid.uuid4())
    columns = [column(kind, selection) for kind in ("source", "topic", "tag")]
    response = client.put(path, json={"columns": columns})
    assert response.status_code == 200
    assert response.json()["columns"] == columns
    assert client.get(path).json()["columns"] == columns


@pytest.mark.parametrize("workspace", ["latest", "my_feed"])
def test_legacy_duplicate_reads_recover_without_rewriting_saved_preferences(
    user_data, database, workspace
):
    client, _, account, *_ = user_data
    selection = uuid.uuid4()
    topic = column("topic", str(selection))
    topic_duplicate = column("topic", selection.hex.upper())
    latest, recommended = column(), column("recommended")
    source, tag = column("source", str(selection)), column("tag", str(selection))
    expected = [topic, latest, recommended, source, tag]
    prefix = "my_feed_" if workspace == "my_feed" else ""
    initial = {
        f"{prefix}columns": [
            topic,
            topic_duplicate,
            latest,
            column(),
            recommended,
            column("recommended"),
            source,
            tag,
        ],
        "unknown": {"preserve": True},
    }
    store(database, account, initial)
    response = client.get(f"{PATH}?workspace={workspace}")
    assert response.status_code == 200
    assert response.json() == {"initialized": True, "columns": expected}
    assert stored(database, account) == initial
    if workspace == "latest":
        assert client.get(FEED_PATH).json()["columns"] == expected
        assert client.get(MY_PATH).json()["columns"] == expected
        # A later Latest edit freezes the recovered prior state into My feed.
        assert client.put(PATH, json={"columns": [column()]}).status_code == 200
        assert client.get(MY_PATH).json()["columns"] == expected
        assert stored(database, account)["my_feed_columns"] == expected
        assert stored(database, account)["unknown"] == initial["unknown"]


def test_new_columns_state_initializes_once_without_changing_other_preferences(user_data, database):
    client, _, account, other, _ = user_data
    settings = {
        "view": "stacked",
        "languages": ["fr"],
        "content_types": ["news"],
        "future_setting": {"preserve": True},
    }
    store(database, account, settings)
    assert client.get(PATH).json() == {"initialized": False, "columns": []}
    assert stored(database, account) == settings
    with database() as session:
        recommendation = session.get(UserRecommendationState, account)
        generation = recommendation.generation
        invalidated = recommendation.invalidated
    columns = [column(), column("recommended")]
    response = client.put(PATH, json={"columns": columns, "mode": "initialize"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"initialized": True, "columns": columns}
    expected = {
        **settings,
        "columns": columns,
        "columns_initialized": True,
        "my_feed_columns": [],
        "my_feed_columns_initialized": False,
    }
    assert stored(database, account) == expected
    assert client.get(PATH).json() == response.json()
    # A second tab uses the persisted identities rather than replacing its first-use state.
    again = client.put(PATH, json={"columns": [column()], "mode": "initialize"})
    assert again.json() == response.json()
    assert stored(database, account) == expected
    assert stored(database, other) == {}
    assert client.get(FEED_PATH).json() == {
        **FeedSettings().model_dump(),
        **{key: settings[key] for key in ("view", "languages", "content_types")},
        "columns": columns,
    }
    with database() as session:
        recommendation = session.get(UserRecommendationState, account)
        assert recommendation.generation == generation
        assert recommendation.invalidated == invalidated


@pytest.mark.parametrize("marker", [None, False, True])
def test_initializers_preserve_configured_columns_even_with_legacy_or_false_marker(
    user_data, database, marker
):
    client, _, account, *_ = user_data
    columns = [column("recommended")]
    settings = {"view": "compact", "columns": columns, "unknown": "retain"}
    if marker is not None:
        settings["columns_initialized"] = marker
    store(database, account, settings)
    assert client.get(PATH).json() == {"initialized": True, "columns": columns}
    for mode in ("initialize", "restore_empty"):
        response = client.put(PATH, json={"columns": [column()], "mode": mode})
        assert response.json() == {"initialized": True, "columns": columns}
        assert stored(database, account) == settings


def test_explicit_empty_and_legacy_empty_are_preserved_until_restored(user_data, database):
    client, _, account, *_ = user_data
    for settings in ({"columns": []}, {"columns": [], "columns_initialized": True}):
        store(database, account, settings)
        assert client.get(PATH).json() == {"initialized": True, "columns": []}
        assert client.put(PATH, json={"columns": [column()], "mode": "initialize"}).json() == {
            "initialized": True,
            "columns": [],
        }
        assert stored(database, account) == settings
        columns = [column()]
        response = client.put(PATH, json={"columns": columns, "mode": "restore_empty"})
        assert response.json() == {"initialized": True, "columns": columns}
        assert client.put(PATH, json={"columns": []}).json() == {
            "initialized": True,
            "columns": [],
        }
        assert stored(database, account)["columns_initialized"] is True


def test_explicit_false_marker_allows_initialization_and_feed_saves_preserve_state(
    user_data, database
):
    client, _, account, *_ = user_data
    settings = {"columns": [], "columns_initialized": False, "unknown": "retain"}
    store(database, account, settings)
    assert client.get(PATH).json() == {"initialized": False, "columns": []}
    assert client.put(FEED_PATH, json={"view": "compact"}).status_code == 200
    assert stored(database, account)["columns_initialized"] is False
    assert client.get(PATH).json() == {"initialized": False, "columns": []}
    columns = [column()]
    assert client.put(PATH, json={"columns": columns, "mode": "initialize"}).json() == {
        "initialized": True,
        "columns": columns,
    }
    assert client.put(FEED_PATH, json={"view": "stacked"}).status_code == 200
    assert client.get(PATH).json() == {"initialized": True, "columns": columns}
    assert stored(database, account)["unknown"] == "retain"
    # Existing clients that explicitly clear columns still express durable intent.
    assert client.put(FEED_PATH, json={"columns": []}).status_code == 200
    assert client.get(PATH).json() == {"initialized": True, "columns": []}
    assert stored(database, account)["columns_initialized"] is True
    assert "columns_initialized" not in client.get(FEED_PATH).json()


@pytest.mark.parametrize("field", ["user_id", "subject", "issuer", "organization_id"])
def test_columns_settings_bind_reads_and_writes_to_the_complete_identity(user_data, field):
    client, current, _, other, _ = user_data
    setattr(current, field, str(other) if field == "user_id" else "wrong-owner")
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json={"columns": []}).status_code == 401


def test_columns_preferences_are_isolated_when_switching_accounts(user_data):
    client, current, first, second, _ = user_data
    columns = [column()]
    assert client.put(PATH, json={"columns": columns}).status_code == 200
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == {"initialized": False, "columns": []}
    assert client.put(PATH, json={"columns": []}).status_code == 200
    current.user_id, current.subject = str(first), "user-a"
    assert client.get(PATH).json() == {"initialized": True, "columns": columns}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"columns": None},
        {"columns": [], "mode": "invalid"},
        {"columns": [], "view": "columns"},
        {"columns": [], "initialized": False},
        {"columns": [], "user_id": str(uuid.uuid4())},
    ],
)
def test_invalid_columns_updates_do_not_change_storage(user_data, database, payload):
    client, _, account, *_ = user_data
    columns = [column()]
    assert client.put(PATH, json={"columns": columns}).status_code == 200
    before = stored(database, account)
    assert client.put(PATH, json=payload).status_code == 422
    assert stored(database, account) == before


def test_concurrent_initializers_return_the_same_saved_columns(user_data, database):
    client, _, account, *_ = user_data
    barrier = Barrier(2)

    def initialize(columns):
        barrier.wait(timeout=5)
        response = client.put(PATH, json={"columns": columns, "mode": "initialize"})
        assert response.status_code == 200
        return response.json()

    candidates = [[column()], [column("recommended")]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(initialize, candidates))
    assert responses[0] == responses[1]
    assert responses[0]["columns"] in candidates
    assert stored(database, account) == {
        "columns": responses[0]["columns"],
        "columns_initialized": True,
        "my_feed_columns": [],
        "my_feed_columns_initialized": False,
    }


@pytest.mark.parametrize(
    "legacy, initialized",
    [
        ({}, False),
        ({"columns": []}, True),
        ({"columns": [], "columns_initialized": True}, True),
        ({"columns": [], "columns_initialized": False}, False),
        ({"columns": [column("recommended")], "columns_initialized": False}, True),
    ],
)
def test_my_feed_reads_legacy_state_without_rewriting_it(user_data, database, legacy, initialized):
    client, _, account, *_ = user_data
    settings = {"view": "columns", "future": {"retain": True}, **legacy}
    store(database, account, settings)
    response = client.get(MY_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"columns": legacy.get("columns", []), "initialized": initialized}
    assert stored(database, account) == settings
    if initialized:
        assert client.put(MY_PATH, json={"columns": [column()], "mode": "initialize"}).json() == (
            response.json()
        )
        assert stored(database, account) == settings


@pytest.mark.parametrize(
    "scoped, initialized",
    [
        ({"my_feed_columns": []}, True),
        ({"my_feed_columns_initialized": False}, False),
        ({"my_feed_columns_initialized": True}, True),
        ({"my_feed_columns": [], "my_feed_columns_initialized": False}, False),
        ({"my_feed_columns": [], "my_feed_columns_initialized": True}, True),
    ],
)
def test_explicit_scoped_emptiness_never_borrows_latest_columns(
    user_data, database, scoped, initialized
):
    client, _, account, *_ = user_data
    latest = [column()]
    settings = {"columns": latest, "columns_initialized": True, **scoped}
    store(database, account, settings)
    assert client.get(MY_PATH).json() == {"columns": [], "initialized": initialized}
    assert client.get(PATH).json() == {"columns": latest, "initialized": True}
    assert stored(database, account) == settings


@pytest.mark.parametrize("my_feed_first", [False, True])
def test_first_use_is_independent_of_which_workspace_opens_first(
    user_data, database, my_feed_first
):
    client, _, account, *_ = user_data
    latest, personal = [column()], [column("recommended")]
    writes = [(PATH, latest), (MY_PATH, personal)]
    if my_feed_first:
        writes.reverse()
    first_path, first_columns = writes[0]
    assert client.put(first_path, json={"columns": first_columns, "mode": "initialize"}).json() == {
        "columns": first_columns,
        "initialized": True,
    }
    assert client.get(writes[1][0]).json() == {"columns": [], "initialized": False}
    second_path, second_columns = writes[1]
    assert client.put(
        second_path, json={"columns": second_columns, "mode": "initialize"}
    ).json() == {
        "columns": second_columns,
        "initialized": True,
    }
    assert client.get(PATH).json() == {"columns": latest, "initialized": True}
    assert client.get(MY_PATH).json() == {"columns": personal, "initialized": True}
    assert stored(database, account) == {
        "columns": latest,
        "columns_initialized": True,
        "my_feed_columns": personal,
        "my_feed_columns_initialized": True,
    }


@pytest.mark.parametrize("write_path", [PATH, f"{PATH}?workspace=latest", FEED_PATH])
@pytest.mark.parametrize("legacy_columns", [[], [column("recommended"), column()]])
def test_latest_writes_snapshot_the_previous_shared_workspace_once(
    user_data, database, write_path, legacy_columns
):
    client, _, account, *_ = user_data
    store(database, account, {"columns": legacy_columns, "unknown": "preserve"})
    latest = [column()]
    assert client.put(write_path, json={"columns": latest}).status_code == 200
    assert client.get(MY_PATH).json() == {"columns": legacy_columns, "initialized": True}
    assert stored(database, account)["my_feed_columns"] == legacy_columns
    assert stored(database, account)["unknown"] == "preserve"
    # Repeated legacy-client saves must not replace the snapshot with the new Latest layout.
    assert client.put(write_path, json={"columns": []}).status_code == 200
    assert client.get(MY_PATH).json() == {"columns": legacy_columns, "initialized": True}
    assert client.get(PATH).json() == {"columns": [], "initialized": True}


def test_scoped_replace_clear_and_restore_never_change_latest_or_global_settings(
    user_data, database
):
    client, _, account, *_ = user_data
    latest, personal = [column()], [column("recommended")]
    settings = {
        "view": "stacked",
        "columns": latest,
        "languages": ["fr"],
        "content_types": ["news"],
        "unknown": {"preserve": True},
    }
    store(database, account, settings)
    assert client.put(MY_PATH, json={"columns": personal}).json() == {
        "columns": personal,
        "initialized": True,
    }
    assert client.put(MY_PATH, json={"columns": []}).json() == {
        "columns": [],
        "initialized": True,
    }
    assert client.put(MY_PATH, json={"columns": personal, "mode": "initialize"}).json() == {
        "columns": [],
        "initialized": True,
    }
    assert client.put(MY_PATH, json={"columns": personal, "mode": "restore_empty"}).json() == {
        "columns": personal,
        "initialized": True,
    }
    assert stored(database, account) == {
        **settings,
        "my_feed_columns": personal,
        "my_feed_columns_initialized": True,
    }
    assert client.get(FEED_PATH).json() == {
        **FeedSettings().model_dump(),
        **{key: settings[key] for key in ("view", "columns", "languages", "content_types")},
    }


@pytest.mark.parametrize("field", ["user_id", "subject", "issuer", "organization_id"])
def test_my_feed_reads_and_writes_require_the_complete_account_identity(user_data, field):
    client, current, _, other, _ = user_data
    setattr(current, field, str(other) if field == "user_id" else "wrong-owner")
    assert client.get(MY_PATH).status_code == 401
    assert client.put(MY_PATH, json={"columns": []}).status_code == 401


def test_invalid_workspace_cannot_read_or_change_columns(user_data, database):
    client, _, account, *_ = user_data
    before = stored(database, account)
    for workspace in ("personal", "", "MY_FEED", str(uuid.uuid4())):
        assert client.get(PATH, params={"workspace": workspace}).status_code == 422
        assert client.put(
            PATH, params={"workspace": workspace}, json={"columns": []}
        ).status_code == (422)
    assert stored(database, account) == before


def test_concurrent_scoped_initialization_preserves_both_configurations(user_data, database):
    client, _, account, *_ = user_data
    barrier = Barrier(2)
    latest, personal = [column()], [column("recommended")]

    def initialize(candidate):
        path, columns = candidate
        barrier.wait(timeout=5)
        response = client.put(path, json={"columns": columns, "mode": "initialize"})
        assert response.status_code == 200
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, [(PATH, latest), (MY_PATH, personal)]))
    assert results == [
        {"columns": latest, "initialized": True},
        {"columns": personal, "initialized": True},
    ]
    assert stored(database, account) == {
        "columns": latest,
        "columns_initialized": True,
        "my_feed_columns": personal,
        "my_feed_columns_initialized": True,
    }
