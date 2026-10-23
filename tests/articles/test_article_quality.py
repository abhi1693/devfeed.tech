from types import SimpleNamespace

import pytest
from devfeed_core.article_quality import identifying_title, presentation_blockers


def presentation(**changes):
    return SimpleNamespace(
        **{
            "title": "Python package management with uv",
            "ai_title": None,
            "ai_summary": "uv creates reproducible Python environments from a lockfile.",
            "ai_description": "A guide to reproducible Python package management.",
            "author": None,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "title",
    ["", "   ", "...", "v0.18.1", "v1", "0.18.1-rc.1", "Release v0.18.1", "Release notes"],
)
def test_bare_versions_and_release_labels_need_an_identifying_title(title):
    assert not identifying_title(title)
    assert presentation_blockers(presentation(title=title)) == ["unidentifiable_title"]


@pytest.mark.parametrize(
    "title",
    ["uv 0.18.1", "Python 3.14 released", "Go", "C++20", "数据库锁定", "What is new in Rust"],
)
def test_identifying_titles_are_not_subject_to_a_word_count_minimum(title):
    assert presentation_blockers(presentation(title=title)) == []


def test_grounded_editorial_title_can_identify_a_version_only_source_title():
    article = presentation(title="v0.18.1", ai_title="uv 0.18.1 improves lockfile resolution")
    assert presentation_blockers(article) == []
    assert article.title == "v0.18.1" and article.author is None


@pytest.mark.parametrize("field", ["ai_summary", "ai_description"])
@pytest.mark.parametrize(
    "value",
    [None, "", " \n ", "--- 123", "<p></p>", "<script>hidden text</script>", "https://example.com"],
)
def test_required_ai_fields_need_readable_content_instead_of_only_markup_or_links(field, value):
    assert presentation_blockers(presentation(**{field: value})) == [f"missing_{field}"]


def test_presentation_readiness_does_not_invent_authors_or_certify_semantic_quality():
    article = presentation(ai_summary="Go.", ai_description="Go.")
    assert presentation_blockers(article) == []
    assert article.author is None


def test_reports_all_missing_prerequisites_without_using_the_feed_teaser_as_ai_prose():
    article = presentation(
        title="Release v0.18.1",
        ai_summary=None,
        ai_description=None,
        summary="A publisher teaser cannot replace DevFeed's generated fields.",
    )
    assert presentation_blockers(article) == [
        "unidentifiable_title",
        "missing_ai_summary",
        "missing_ai_description",
    ]
