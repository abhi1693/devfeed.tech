import json
import re

import pytest
from devfeed_core.source_tags import source_tag_names, source_tag_slug


@pytest.mark.parametrize(
    "raw, name, slug",
    [
        ('"audits"', "audits", "audits"),
        ("“#react”", "react", "react"),
        ("#career   development", "career development", "career-development"),
        ("#C#", "C#", "c-sharp"),
        ("C++", "C++", "c-plus-plus"),
        (".NET 11", ".NET 11", "net-11"),
        ("developer's tools", "developer's tools", "developer-s-tools"),
    ],
)
def test_feed_presentation_markers_do_not_become_tag_identity(raw, name, slug):
    assert source_tag_names([raw]) == {name.casefold(): name}
    assert source_tag_slug(raw) == slug


def test_empty_markers_are_ignored_and_equivalent_labels_deduplicate():
    assert source_tag_names(["###", '""', "#", "#React", "React", '"React"']) == {"react": "React"}


def test_admin_tag_inputs_share_name_normalization():
    from devfeed_core.schemas import TagPatch, TagWrite

    assert TagWrite(name='"#React"', slug="react").name == "React"
    assert TagPatch(name="#C#").name == "C#"
    with pytest.raises(ValueError):
        TagPatch(name="###")


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": "Python"},
        {"slug": "python"},
        {"aliases": []},
        {"auto_link_topic": False},
        {"topic_id": None},
        {"aliases": [], "auto_link_topic": False, "topic_id": None},
    ],
)
def test_tag_patch_serialization_preserves_omission_clear_and_falsy_changes(changes):
    from devfeed_core.schemas import TagPatch

    patch = TagPatch.model_validate(changes)
    assert patch.model_dump() == changes
    assert json.loads(patch.model_dump_json()) == changes
    replay = TagPatch.model_validate_json(patch.model_dump_json())
    assert replay.model_fields_set == set(changes)


@pytest.mark.parametrize("field", ["name", "slug", "aliases", "auto_link_topic"])
def test_tag_patch_contract_does_not_advertise_invalid_null_updates(field):
    from devfeed_admin_api.main import create_app
    from devfeed_core.schemas import TagPatch
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError

    schema = create_app().openapi()["components"]["schemas"]["TagPatch"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid({})
    assert validator.is_valid({"aliases": [], "auto_link_topic": False, "topic_id": None})
    assert not validator.is_valid({field: None})
    with pytest.raises(ValidationError, match="Only topic_id can be null"):
        TagPatch.model_validate({field: None})


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"slug": "x" * 101},
        {"slug": "Invalid Slug"},
        {"aliases": [f"alias-{number}" for number in range(101)]},
    ],
)
def test_tag_patch_runtime_and_contract_reject_invalid_bounds(changes):
    from devfeed_core.schemas import TagPatch
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError

    validator = Draft202012Validator(TagPatch.model_json_schema())
    assert not validator.is_valid(changes)
    with pytest.raises(ValidationError):
        TagPatch.model_validate(changes)
    boundary = {
        "name": "x" * 100,
        "slug": "x" * 100,
        "aliases": [f"alias-{number}" for number in range(100)],
    }
    validator.validate(boundary)
    assert TagPatch.model_validate(boundary).model_dump() == boundary


def test_source_tag_names_are_bounded_clean_and_case_insensitive():
    assert source_tag_names([" Python ", "PYTHON", "<b>Web   Dev</b>", "", "!!!", None, 42]) == {
        "python": "Python",
        "web dev": "Web Dev",
    }
    assert list(source_tag_names(["x" * 200]).values()) == ["x" * 100]
    assert source_tag_names(["ＦａｓｔＡＰＩ", "FastAPI"]) == {"fastapi": "FastAPI"}


@pytest.mark.parametrize("name", ["C++", "C#", "C", "日本語", "é", "very long " * 30])
def test_source_tag_slugs_fit_the_catalog_contract(name):
    slug = source_tag_slug(name)
    assert len(slug) <= 100 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
    assert source_tag_slug(name.upper()) == slug


def test_source_tag_slugs_do_not_collapse_language_names_or_non_latin_labels():
    assert len({source_tag_slug(name) for name in ["C", "C++", "C#", "日本語", "中文"]}) == 5
    assert len({source_tag_slug(name) for name in ["AI", "AI 日本語", "AI 中文"]}) == 3


@pytest.fixture
def shared_projection(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import Mock

    from devfeed_core import cache, source_tags

    redis = Mock()
    redis.getrange.return_value = b""
    client = cache.ResponseCache(redis, "source-tag-test")
    monkeypatch.setattr(source_tags, "get_cache", lambda: client)
    monkeypatch.setattr(
        source_tags,
        "get_settings",
        lambda: SimpleNamespace(cache_enabled=True, source_tag_shared_cache_enabled=True),
    )
    monkeypatch.setattr(source_tags, "_identity_snapshot", None)
    identifier, revision = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(id=identifier, name="Fast API", slug="fastapi", aliases=["fast"])
    session = Mock()
    session.scalar.return_value = revision
    session.execute.return_value.all.return_value = [row]
    return SimpleNamespace(
        module=source_tags,
        client=client,
        redis=redis,
        identifier=identifier,
        revision=revision,
        row=row,
        session=session,
    )


def test_source_tag_projection_is_shared_without_repeating_normalization(shared_projection):
    v = shared_projection
    expected = v.module._source_tag_index(v.session)
    assert expected[0]["fast"] == v.identifier
    payload = v.redis.set.call_args.args[1]
    assert v.redis.set.call_args.args[0] == "source-tag-test:source-tag-identities:v1"
    assert v.redis.set.call_args.kwargs == {"ex": 600}
    assert v.session.execute.call_count == 1
    # The local snapshot needs no transport or catalog reload.
    assert v.module._source_tag_index(v.session) == expected
    assert v.redis.getrange.call_count == 1
    v.module._identity_snapshot = None
    v.redis.getrange.return_value = payload
    assert v.module._source_tag_index(v.session) == expected
    assert v.session.execute.call_count == 1
    v.redis.getrange.assert_called_with(
        "source-tag-test:source-tag-identities:v1", 0, v.module._SHARED_INDEX_BYTES
    )
    with pytest.raises(TypeError):
        expected[0]["other"] = v.identifier


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "string_transport",
        "too_many_ids",
        "wire_oversized",
        "bad_compression",
        "truncated",
        "trailing",
        "decoded_oversized",
        "invalid_json",
        "stale",
        "ids_not_list",
        "non_string_id",
        "invalid_uuid",
        "map_not_dict",
        "empty_key",
        "long_key",
        "bool_reference",
        "negative_reference",
        "large_reference",
    ],
)
def test_invalid_shared_source_tag_data_is_never_used(shared_projection, monkeypatch, invalid):
    import zlib

    v = shared_projection
    value = {
        "revision": str(v.revision),
        "ids": [str(v.identifier)],
        "identities": {"fast": 0},
        "slugs": {"fastapi": 0},
    }
    if invalid == "stale":
        value["revision"] = "older"
    elif invalid == "too_many_ids":
        monkeypatch.setattr(v.module, "_SHARED_INDEX_MAX_TAGS", 0)
    elif invalid == "ids_not_list":
        value["ids"] = {}
    elif invalid == "non_string_id":
        value["ids"] = [42]
    elif invalid == "invalid_uuid":
        value["ids"] = ["invalid"]
    elif invalid == "map_not_dict":
        value["identities"] = []
    elif invalid in {"empty_key", "long_key"}:
        value["identities"] = {"" if invalid == "empty_key" else "x" * 301: 0}
    elif invalid in {"bool_reference", "negative_reference", "large_reference"}:
        value["identities"] = {
            "fast": {"bool_reference": True, "negative_reference": -1, "large_reference": 9}[
                invalid
            ]
        }
    body = zlib.compress(json.dumps(value).encode())
    if invalid == "missing":
        body = b""
    elif invalid == "string_transport":
        body = "not compressed bytes"
    elif invalid == "wire_oversized":
        monkeypatch.setattr(v.module, "_SHARED_INDEX_BYTES", 16)
        body = b"x" * 17
    elif invalid == "bad_compression":
        body = b"invalid"
    elif invalid == "truncated":
        body = body[:-1]
    elif invalid == "trailing":
        body += b"extra"
    elif invalid == "decoded_oversized":
        monkeypatch.setattr(v.module, "_SHARED_INDEX_DECODED_BYTES", 32)
    elif invalid == "invalid_json":
        body = zlib.compress(b"not json")
    v.redis.getrange.return_value = body
    assert v.module._shared_source_tag_index(v.revision) is None


def test_unavailable_shared_source_tag_transport_does_not_block_database_resolution(
    shared_projection,
):
    from redis.exceptions import ConnectionError

    v = shared_projection
    v.redis.getrange.side_effect = ConnectionError("unavailable")
    assert v.module._source_tag_index(v.session)[0]["fast"] == v.identifier
    assert v.session.execute.call_count == 1
    v.redis.set.assert_not_called()


@pytest.mark.parametrize(
    "limit", ["_SHARED_INDEX_BYTES", "_SHARED_INDEX_DECODED_BYTES", "_SHARED_INDEX_MAX_TAGS"]
)
def test_oversized_source_tag_projection_is_not_published(shared_projection, monkeypatch, limit):
    v = shared_projection
    monkeypatch.setattr(v.module, limit, 0 if limit == "_SHARED_INDEX_MAX_TAGS" else 8)
    assert v.module._source_tag_index(v.session)[0]["fast"] == v.identifier
    v.redis.set.assert_not_called()


def test_source_tag_catalog_change_during_shared_lookup_uses_fresh_database_rows(shared_projection):
    import uuid

    v = shared_projection
    v.module._source_tag_index(v.session)
    v.redis.getrange.return_value = v.redis.set.call_args.args[1]
    v.redis.set.reset_mock()
    v.module._identity_snapshot = None
    newer = uuid.uuid4()
    v.session.scalar.side_effect = [v.revision, newer, newer]
    v.row.name = "Changed name"
    found = v.module._source_tag_index(v.session)
    assert found[0]["changed name"] == v.identifier
    assert "fast api" not in found[0]
    assert v.module._identity_snapshot is None
    v.redis.set.assert_not_called()


@pytest.mark.parametrize("public_cache", [False, True])
def test_disabled_shared_source_tag_projection_keeps_existing_local_policy(
    shared_projection, monkeypatch, public_cache
):
    from types import SimpleNamespace

    v = shared_projection
    monkeypatch.setattr(
        v.module,
        "get_settings",
        lambda: SimpleNamespace(
            cache_enabled=public_cache,
            source_tag_shared_cache_enabled=False,
        ),
    )
    assert v.module._source_tag_index(v.session)[0]["fast"] == v.identifier
    v.redis.getrange.assert_not_called()
    v.redis.set.assert_not_called()


def test_shared_projection_preserves_expanding_unicode_casefolds(shared_projection):
    v = shared_projection
    v.row.name = "ΐ" * 100
    expected = v.module._source_tag_index(v.session)
    key = next(iter(source_tag_names([v.row.name])))
    assert len(key) == 300 and expected[0][key] == v.identifier
    v.redis.getrange.return_value = v.redis.set.call_args.args[1]
    v.module._identity_snapshot = None
    assert v.module._source_tag_index(v.session)[0][key] == v.identifier
    assert v.session.execute.call_count == 1
