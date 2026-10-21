"""Strict reader-layout writes and defensive, non-destructive saved preference reads."""

import copy
import uuid

import pytest
from devfeed_core.user_settings import FeedSettings, normalize_feed_settings
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
        for index, kind in enumerate(
            ["latest", "recommended", "source", "topic", "tag", "latest"], start=1
        )
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
    assert (
        len(FeedSettings.model_validate({"columns": [column(i) for i in range(12)]}).columns) == 12
    )


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
    valid = [column(index) for index in range(1, 15)]
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
    assert stored == original


@pytest.mark.parametrize("stored", [None, [], "invalid", {}, {"columns": {}}, {"columns": None}])
def test_malformed_or_missing_saved_preferences_have_safe_defaults(stored):
    assert normalize_feed_settings(stored) == FeedSettings()


def test_bad_legacy_filters_do_not_discard_valid_layout_preferences():
    settings = normalize_feed_settings(
        {"view": "stacked", "columns": [column()], "languages": [], "content_types": ["bad"]}
    )
    assert settings.view == "stacked"
    assert settings.columns
    assert settings.languages == FeedSettings().languages
    assert settings.content_types == FeedSettings().content_types
