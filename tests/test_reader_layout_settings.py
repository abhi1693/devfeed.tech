"""Strict reader-layout writes and defensive, non-destructive saved preference reads."""

import copy
import uuid

import pytest
from devfeed_core.user_settings import (
    FeedSettings,
    ReaderColumnsState,
    ReaderColumnStarters,
    ReaderColumnsUpdate,
    normalize_feed_settings,
    preserve_my_feed_columns,
    reader_columns_state,
)
from pydantic import ValidationError

COLUMN_ID = "1cd602c8-ade0-4b19-8f61-448a679f5f45"
SELECTION_ID = "4fe9df76-f272-48cb-81ac-2545dc60a636"


def column(index=1, kind="latest"):
    feed = {"kind": kind}
    if kind in {"source", "topic", "tag"}:
        feed["id"] = SELECTION_ID
    return {"id": str(uuid.UUID(int=index)), "feed": feed}


@pytest.mark.parametrize("view", ["cards", "compact", "stacked", "columns"])
def test_all_reader_layouts_preserve_ordered_columns_and_unavailable_selections(view):
    columns = [
        column(index, kind)
        for index, kind in enumerate(["latest", "recommended", "source", "topic", "tag"], start=1)
    ]
    settings = FeedSettings.model_validate({"view": view, "columns": columns})
    assert settings.model_dump(mode="json")["columns"] == columns
    assert settings.view == view


@pytest.mark.parametrize(
    "payload",
    [
        {"view": "future"},
        {"columns": None},
        {"columns": {}},
        {"columns": [None]},
        {"columns": [{}]},
        {"columns": [{"id": "invalid", "feed": {"kind": "latest"}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "unknown"}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "source"}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "topic", "id": None}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "tag", "id": "invalid"}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "latest", "id": SELECTION_ID}}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "recommended", "id": SELECTION_ID}}]},
        {"columns": [{**column(), "title": "Unexpected"}]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "latest", "url": "https://x.test"}}]},
        {
            "columns": [
                {
                    "id": COLUMN_ID,
                    "feed": {"kind": "source", "id": SELECTION_ID, "name": "Unexpected"},
                }
            ]
        },
        {"columns": [column(index) for index in range(13)]},
        {"columns": [column(), column()]},
        {
            "columns": [
                {"id": COLUMN_ID, "feed": {"kind": "latest"}},
                {"id": uuid.UUID(COLUMN_ID).hex, "feed": {"kind": "recommended"}},
            ]
        },
        {"columns": [], "import_guest": True},
    ],
)
def test_invalid_reader_layout_writes_are_rejected(payload):
    with pytest.raises(ValidationError):
        FeedSettings.model_validate(payload)


def test_uuid_forms_are_canonicalized_and_twelve_columns_are_allowed():
    settings = FeedSettings.model_validate(
        {
            "columns": [
                {
                    "id": COLUMN_ID.upper(),
                    "feed": {"kind": "source", "id": uuid.UUID(SELECTION_ID).hex},
                }
            ]
        }
    )
    assert settings.model_dump(mode="json")["columns"] == [
        {"id": COLUMN_ID, "feed": {"kind": "source", "id": SELECTION_ID}}
    ]
    columns = [
        {"id": str(uuid.UUID(int=i)), "feed": {"kind": "topic", "id": str(uuid.UUID(int=i + 100))}}
        for i in range(12)
    ]
    assert len(FeedSettings.model_validate({"columns": columns}).columns) == 12


@pytest.mark.parametrize("view", ["future", None, {}, 12])
def test_saved_unknown_views_fall_back_without_losing_valid_columns(view):
    stored = {
        "view": view,
        "languages": ["fr", "fr"],
        "content_types": ["tutorial", "article"],
        "columns": [column(1, "source"), column(2, "recommended")],
        "future_setting": {"untouched": True},
    }
    original = copy.deepcopy(stored)
    settings = normalize_feed_settings(stored)
    assert settings.view == "cards"
    assert settings.languages == ["fr"]
    assert settings.content_types == ["article", "tutorial"]
    assert settings.model_dump(mode="json")["columns"] == stored["columns"]
    assert stored == original


def test_saved_columns_keep_the_first_twelve_valid_unique_entries_without_mutation():
    valid = [
        {
            "id": str(uuid.UUID(int=index)),
            "feed": {"kind": "topic", "id": str(uuid.UUID(int=index + 100))},
        }
        for index in range(1, 15)
    ]
    stored = {
        "view": "columns",
        "columns": [
            None,
            {},
            valid[0],
            {**valid[0], "id": uuid.UUID(valid[0]["id"]).hex},
            {**column(30, "source"), "feed": {"kind": "source"}},
            *valid[1:],
        ],
    }
    original = copy.deepcopy(stored)
    settings = normalize_feed_settings(stored)
    assert settings.view == "columns"
    assert settings.model_dump(mode="json")["columns"] == valid[:12]
    for workspace in ("latest", "my_feed"):
        assert (
            reader_columns_state(stored, workspace).model_dump(mode="json")["columns"] == valid[:12]
        )
    assert stored == original


@pytest.mark.parametrize("stored", [None, [], "invalid", {}, {"columns": {}}, {"columns": None}])
def test_malformed_or_missing_saved_preferences_have_safe_defaults(stored):
    assert normalize_feed_settings(stored) == FeedSettings()


def test_bad_legacy_filters_do_not_discard_valid_layout_preferences():
    stored = {"view": "stacked", "columns": [column()], "languages": [], "content_types": ["bad"]}
    settings = normalize_feed_settings(stored)
    assert settings.view == "stacked"
    assert settings.columns
    assert settings.languages == FeedSettings().languages
    assert settings.content_types == FeedSettings().content_types
    assert reader_columns_state(stored).columns == settings.columns


@pytest.mark.parametrize(
    "stored, initialized",
    [
        (None, False),
        ([], False),
        ({}, False),
        ({"columns": None}, False),
        ({"columns": {}}, False),
        ({"columns": []}, True),
        ({"columns": [None, {"id": "broken"}]}, True),
        ({"columns_initialized": True}, True),
        ({"columns_initialized": False}, False),
        ({"columns": [], "columns_initialized": False}, False),
        ({"columns": [], "columns_initialized": True}, True),
        ({"columns": [column()], "columns_initialized": False}, True),
    ],
)
def test_columns_state_distinguishes_first_use_from_conservative_legacy_emptiness(
    stored, initialized
):
    original = copy.deepcopy(stored)
    state = reader_columns_state(stored)
    assert state.initialized is initialized
    assert state.columns == normalize_feed_settings(stored).columns
    assert stored == original
    assert set(normalize_feed_settings(stored).model_dump()) == {
        "view",
        "columns",
        "languages",
        "content_types",
    }


@pytest.mark.parametrize("mode", ["replace", "initialize", "restore_empty"])
def test_columns_updates_reuse_strict_identity_and_order_validation(mode):
    columns = [column(1), column(2, "topic")]
    payload = ReaderColumnsUpdate.model_validate({"columns": columns, "mode": mode})
    assert payload.model_dump(mode="json") == {"columns": columns, "mode": mode}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"columns": None},
        {"columns": [column(), column()]},
        {"columns": [column(index) for index in range(13)]},
        {"columns": [{"id": COLUMN_ID, "feed": {"kind": "source"}}]},
        {"columns": [], "mode": "unknown"},
        {"columns": [], "mode": None},
        {"columns": [], "initialized": False},
        {"columns": [], "columns_initialized": False},
        {"columns": [], "view": "columns"},
    ],
)
def test_invalid_columns_state_updates_are_rejected(payload):
    with pytest.raises(ValidationError):
        ReaderColumnsUpdate.model_validate(payload)


@pytest.mark.parametrize("model", [FeedSettings, ReaderColumnsState, ReaderColumnsUpdate])
@pytest.mark.parametrize("kind", ["latest", "recommended", "source", "topic", "tag"])
def test_duplicate_feed_selections_are_rejected_even_with_distinct_column_ids(model, kind):
    columns = [column(1, kind), column(2, kind)]
    if kind in {"source", "topic", "tag"}:
        columns[1]["feed"]["id"] = uuid.UUID(SELECTION_ID).hex.upper()
    payload = {"columns": columns}
    if model is ReaderColumnsState:
        payload["initialized"] = True
    with pytest.raises(ValidationError, match="Each feed can appear only once"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", [FeedSettings, ReaderColumnsState, ReaderColumnsUpdate])
def test_distinct_entity_kinds_can_share_the_same_selection_uuid(model):
    columns = [
        column(index, kind)
        for index, kind in enumerate(["source", "topic", "tag", "latest", "recommended"], start=1)
    ]
    payload = {"columns": columns}
    if model is ReaderColumnsState:
        payload["initialized"] = True
    assert model.model_validate(payload).model_dump(mode="json")["columns"] == columns


@pytest.mark.parametrize("workspace", ["latest", "my_feed"])
def test_legacy_duplicate_feeds_keep_first_stable_ids_and_order_without_mutating_storage(workspace):
    first = column(1, "topic")
    duplicate = column(2, "topic")
    duplicate["feed"]["id"] = uuid.UUID(SELECTION_ID).hex.upper()
    latest, recommended = column(3), column(5, "recommended")
    source, tag = column(7, "source"), column(8, "tag")
    # A skipped duplicate must not consume the stable ID needed by a later unique feed.
    next_topic = {
        "id": duplicate["id"],
        "feed": {"kind": "topic", "id": str(uuid.UUID(int=999))},
    }
    raw_columns = [
        first,
        duplicate,
        {"id": first["id"], "feed": source["feed"]},
        latest,
        column(4),
        recommended,
        column(6, "recommended"),
        source,
        tag,
        next_topic,
    ]
    prefix = "my_feed_" if workspace == "my_feed" else ""
    stored = {f"{prefix}columns": raw_columns, "future_setting": {"preserve": True}}
    original = copy.deepcopy(stored)
    expected = [first, latest, recommended, source, tag, next_topic]
    assert reader_columns_state(stored, workspace).model_dump(mode="json") == {
        "initialized": True,
        "columns": expected,
    }
    if workspace == "latest":
        assert normalize_feed_settings(stored).model_dump(mode="json")["columns"] == expected
        assert (
            reader_columns_state(stored, "my_feed").model_dump(mode="json")["columns"] == expected
        )
        frozen = preserve_my_feed_columns(stored)
        assert frozen["my_feed_columns"] == expected
        assert frozen["my_feed_columns_initialized"] is True
    assert stored == original


@pytest.mark.parametrize(
    "scoped, initialized, expected_columns",
    [
        ({}, True, [column(1), column(2, "recommended")]),
        ({"my_feed_columns": []}, True, []),
        ({"my_feed_columns_initialized": False}, False, []),
        ({"my_feed_columns_initialized": True}, True, []),
        ({"my_feed_columns": [], "my_feed_columns_initialized": False}, False, []),
        ({"my_feed_columns": [column(3, "topic")]}, True, [column(3, "topic")]),
    ],
)
def test_scoped_columns_use_legacy_only_when_both_scope_keys_are_absent(
    scoped, initialized, expected_columns
):
    value = {
        "view": "columns",
        "columns": [column(1), column(2, "recommended")],
        "future_setting": {"untouched": True},
        **scoped,
    }
    original = copy.deepcopy(value)
    assert reader_columns_state(value, "my_feed").model_dump(mode="json") == {
        "columns": expected_columns,
        "initialized": initialized,
    }
    assert reader_columns_state(value).model_dump(mode="json")["columns"] == value["columns"]
    assert value == original


@pytest.mark.parametrize("marker", [None, False, True])
def test_scoped_reads_recover_valid_columns_without_rewriting_saved_unknown_fields(marker):
    value = {
        "columns": [column(1)],
        "my_feed_columns": [None, column(2, "topic"), column(2, "topic"), {"id": "bad"}],
        "my_feed_columns_initialized": marker,
        "future_setting": {"untouched": True},
    }
    original = copy.deepcopy(value)
    assert reader_columns_state(value, "my_feed").model_dump(mode="json") == {
        "columns": [column(2, "topic")],
        "initialized": True,
    }
    assert value == original


@pytest.mark.parametrize(
    "legacy, initialized",
    [
        ({}, False),
        ({"columns": []}, True),
        ({"columns": [], "columns_initialized": False}, False),
        ({"columns": [column(1), column(2, "topic")]}, True),
    ],
)
def test_freezing_the_legacy_workspace_preserves_identity_order_and_empty_intent(
    legacy, initialized
):
    value = {"future_setting": {"untouched": True}, **legacy}
    original = copy.deepcopy(value)
    frozen = preserve_my_feed_columns(value)
    assert frozen == {
        **value,
        "my_feed_columns": legacy.get("columns", []),
        "my_feed_columns_initialized": initialized,
    }
    assert value == original
    changed_latest = {**frozen, "columns": [column(3, "recommended")]}
    assert preserve_my_feed_columns(changed_latest) == changed_latest


@pytest.mark.parametrize(
    "scoped",
    [
        {"my_feed_columns": []},
        {"my_feed_columns_initialized": False},
        {"my_feed_columns": [column(2, "topic")], "my_feed_columns_initialized": True},
    ],
)
def test_existing_scoped_state_is_authoritative_even_when_partially_saved(scoped):
    value = {"columns": [column(1)], **scoped}
    original = copy.deepcopy(value)
    assert preserve_my_feed_columns(value) == value
    assert value == original


def test_personal_starter_receipt_accepts_four_canonical_catalog_selections():
    feeds = [
        {"kind": kind, "id": str(uuid.UUID(int=index))}
        for index, kind in enumerate(("topic", "source", "tag", "topic"), start=1)
    ]
    assert ReaderColumnStarters.model_validate({"feeds": feeds}).model_dump(mode="json") == {
        "feeds": feeds
    }


@pytest.mark.parametrize(
    "feeds",
    [
        None,
        [{"kind": "latest"}],
        [{"kind": "recommended"}],
        [{"kind": "topic", "id": "invalid"}],
        [{"kind": "topic", "id": SELECTION_ID, "url": "https://unexpected.example"}],
        [{"kind": "topic", "id": SELECTION_ID}] * 5,
    ],
)
def test_personal_starter_receipts_reject_unknown_or_unbounded_selections(feeds):
    with pytest.raises(ValidationError):
        ReaderColumnStarters.model_validate({"feeds": feeds})
