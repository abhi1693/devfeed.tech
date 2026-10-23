"""Bounded semantic layout scopes and defensive per-feed preference recovery."""

import copy
import uuid
from typing import get_args

import pytest
from devfeed_core.schemas import ContentType
from devfeed_core.user_settings import (
    FeedSettings,
    FeedViewScope,
    FeedViewSettings,
    normalize_feed_view_scope,
    reader_feed_view,
    update_reader_feed_view,
)
from pydantic import TypeAdapter, ValidationError

IDENTIFIER = uuid.UUID("12345678-abcd-4abc-8abc-123456789abc")
SCOPES = [
    "my_feed",
    "latest",
    "trending",
    "read_later",
    "search",
    *[f"content:{kind}" for kind in get_args(ContentType)],
    *[f"{kind}:{IDENTIFIER}" for kind in ("topic", "source", "tag")],
]


@pytest.mark.parametrize("scope", SCOPES)
def test_supported_scopes_are_stable_semantic_identities(scope):
    assert normalize_feed_view_scope(scope) == scope
    assert TypeAdapter(FeedViewScope).validate_python(scope) == scope


@pytest.mark.parametrize("kind", ["topic", "source", "tag"])
@pytest.mark.parametrize("identifier", [str(IDENTIFIER).upper(), IDENTIFIER.hex, IDENTIFIER.urn])
def test_entity_scope_uuid_spellings_canonicalize_to_one_identity(kind, identifier):
    assert normalize_feed_view_scope(f"{kind}:{identifier}") == f"{kind}:{IDENTIFIER}"


@pytest.mark.parametrize(
    "scope",
    [
        "",
        "/",
        "/latest",
        "LATEST",
        "latest?cursor=private",
        "search:private query",
        "content:video",
        "content:articles",
        "source:",
        "source:publisher-slug",
        "topic:invalid",
        "tag:c++",
        "unknown:" + str(IDENTIFIER),
        "topic:" + str(IDENTIFIER) + ":article",
        "latest ",
        "\nlatest",
        "x" * 65,
    ],
)
def test_arbitrary_routes_queries_and_unknown_scopes_are_rejected(scope):
    with pytest.raises(ValueError):
        normalize_feed_view_scope(scope)
    with pytest.raises(ValidationError):
        TypeAdapter(FeedViewScope).validate_python(scope)


@pytest.mark.parametrize(
    "payload", [{}, {"view": None}, {"view": "future"}, {"view": "cards", "feed": "latest"}]
)
def test_layout_writes_accept_only_the_declared_view(payload):
    with pytest.raises(ValidationError):
        FeedViewSettings.model_validate(payload)


@pytest.mark.parametrize("stored", [None, [], "future", {}, {"view": None}, {"view": "future"}])
def test_absent_and_malformed_settings_recover_to_grid_without_mutation(stored):
    before = copy.deepcopy(stored)
    assert reader_feed_view(stored, "latest").model_dump() == {"view": "cards"}
    assert stored == before


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({"view": "compact"}, "compact"),
        ({"view": "columns"}, "cards"),
        ({"view": "compact", "feed_view_default": "stacked"}, "stacked"),
        ({"view": "compact", "feed_view_default": "future"}, "compact"),
        ({"view": "compact", "feed_views": []}, "compact"),
        ({"view": "compact", "feed_views": {"latest": "future"}}, "compact"),
        ({"view": "compact", "feed_views": {"latest": None}}, "compact"),
        ({"view": "compact", "feed_views": {"latest": {"view": "stacked"}}}, "compact"),
        ({"view": "compact", "feed_views": {"latest": "stacked"}}, "stacked"),
        ({"view": "compact", "feed_views": {"latest": "columns"}}, "cards"),
    ],
)
def test_scoped_overrides_then_frozen_default_then_legacy_view_determine_layout(stored, expected):
    before = copy.deepcopy(stored)
    assert reader_feed_view(stored, "latest").view == expected
    assert stored == before


@pytest.mark.parametrize("scope", SCOPES)
def test_columns_is_recovered_only_for_my_feed_and_rejected_elsewhere(scope):
    stored = {"view": "columns"}
    assert reader_feed_view(stored, scope).view == ("columns" if scope == "my_feed" else "cards")
    if scope == "my_feed":
        assert update_reader_feed_view(stored, scope, FeedViewSettings(view="columns"))[
            "feed_views"
        ] == {"my_feed": "columns"}
    else:
        with pytest.raises(ValueError, match="only on My feed"):
            update_reader_feed_view(stored, scope, FeedViewSettings(view="columns"))


def test_scope_update_freezes_default_without_changing_legacy_or_other_preferences():
    stored = {
        "view": "stacked",
        "languages": ["fr"],
        "content_types": ["news"],
        "my_feed_columns": [],
        "my_feed_columns_initialized": True,
        "future": {"preserve": True},
        "feed_views": {"future_scope": "future_layout"},
    }
    before = copy.deepcopy(stored)
    saved = update_reader_feed_view(stored, "my_feed", FeedViewSettings(view="columns"))
    assert saved == {
        **stored,
        "feed_view_default": "stacked",
        "feed_views": {"future_scope": "future_layout", "my_feed": "columns"},
    }
    later = update_reader_feed_view(
        {**saved, "view": "compact"}, "latest", FeedViewSettings(view="cards")
    )
    assert reader_feed_view(later, "my_feed").view == "columns"
    assert reader_feed_view(later, "latest").view == "cards"
    assert reader_feed_view(later, "search").view == "stacked"
    assert stored == before
    assert set(FeedSettings.model_validate({}).model_dump()) == {
        "view",
        "columns",
        "languages",
        "content_types",
    }


def test_custom_scope_limit_preserves_existing_choices_and_allows_updates_at_capacity():
    views = {f"topic:{uuid.UUID(int=index)}": "cards" for index in range(256)}
    stored = {"feed_views": views, "feed_view_default": "compact"}
    before = copy.deepcopy(stored)
    with pytest.raises(ValueError, match="256"):
        update_reader_feed_view(stored, "latest", FeedViewSettings(view="stacked"))
    saved = update_reader_feed_view(stored, next(iter(views)), FeedViewSettings(view="stacked"))
    assert len(saved["feed_views"]) == 256
    assert reader_feed_view(saved, next(iter(views))).view == "stacked"
    assert stored == before
