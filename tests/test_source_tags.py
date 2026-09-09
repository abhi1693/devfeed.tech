import re

import pytest
from devfeed_core.source_tags import source_tag_names, source_tag_slug


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
