"""Publisher documents stay bounded while safe metadata fallbacks remain usable."""

import io
import json
import zipfile
from unittest.mock import Mock

import pytest
from devfeed_aggregator.article_pages import excerpt
from devfeed_core import github_topics, images, source_inspection
from devfeed_core.feeds.fetcher import FetchResult

from tests.taxonomy.test_github_topics import DOCUMENT, REVISION

URL = "https://publisher.example/post"
SAFE_IMAGE = "https://publisher.example/preview.png"


def page(html):
    return FetchResult(200, html.encode(), URL, content_type="text/html; charset=utf-8")


@pytest.mark.parametrize("property_name", ["og:image", "twitter:image"])
def test_preview_metadata_cap_skips_excess_candidates_and_retains_a_safe_structured_fallback(
    property_name,
):
    html = (
        f'<meta property="{property_name}" content="http://127.0.0.1/private.png">' * 30
        + f'<meta property="{property_name}" content="https://publisher.example/excess.png">'
        + '<script type="application/ld+json">'
        + json.dumps({"@type": "Article", "image": SAFE_IMAGE})
        + "</script>"
    )

    image = images.extract_image(page(html))

    assert image is not None and image.url == SAFE_IMAGE and image.method == "json_ld"


def test_excess_structured_documents_cannot_supply_an_image_after_the_processing_limit():
    html = (
        '<script type="application/ld+json">{"@type":"Organization"}</script>' * 20
        + '<script type="application/ld+json">'
        + json.dumps({"@type": "Article", "image": SAFE_IMAGE})
        + "</script>"
    )

    assert images.extract_image(page(html)) is None


def test_oversized_structured_document_does_not_poison_a_later_bounded_image_document():
    html = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Article",
                "image": "https://publisher.example/oversized.png",
                "text": "x" * 200_000,
            }
        )
        + '</script><script type="application/ld+json">'
        + json.dumps({"@type": "Article", "image": SAFE_IMAGE})
        + "</script>"
    )

    image = images.extract_image(page(html))

    assert image is not None and image.url == SAFE_IMAGE and image.method == "json_ld"


def test_website_profile_ignores_stylesheets_while_retaining_the_declared_icon():
    fetch = Mock(
        return_value=page(
            '<html lang="en"><head><link rel="stylesheet" href="/style.css">'
            '<link rel="icon" href="/icon.png"></head></html>'
        )
    )

    profile = source_inspection.complete_website_profile({}, URL, fetch=fetch)

    assert profile["logo_url"] == "https://publisher.example/icon.png"
    assert profile["language"] == "en" and profile["website_url"] == URL


def test_long_preview_without_sentence_boundaries_stops_at_a_complete_word():
    result = excerpt("Detailed " * 100)

    assert len(result) <= 500 and result.endswith("Detailed…")
    assert set(result.removesuffix("…").split()) == {"Detailed"}


def test_excess_repository_entries_fail_before_importing_even_a_valid_topic(monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("explore/topics/python/index.md", DOCUMENT)
        for index in range(20_000):
            archive.writestr(f"explore/unrelated/{index}", b"")
    monkeypatch.setattr(github_topics, "read_document", lambda *_args, **_kw: payload.getvalue())
    github_topics.repository_topics.cache_clear()
    try:
        with pytest.raises(github_topics.GitHubUnavailable, match="No topics were imported"):
            github_topics.repository_topics(REVISION)
    finally:
        github_topics.repository_topics.cache_clear()
