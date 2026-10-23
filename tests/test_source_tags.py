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
