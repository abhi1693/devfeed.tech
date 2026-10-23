"""Malformed publisher documents cannot invent labels or executable redirect targets."""

import json
from html import escape

import pytest
from devfeed_aggregator import article_pages as pages
from devfeed_core.feeds.fetcher import FetchResult

URL = "https://publisher.example/article"


def labels(html):
    parser = pages.MetadataParser(URL)
    parser.feed(html)
    parser.close()
    return pages.article_tags(html, parser)


def redirect_page(target, *, refresh=None):
    instruction = f"0;url={target}" if refresh is None else refresh
    return FetchResult(
        200,
        (
            '<html><head><meta http-equiv="refresh" '
            f'content="{escape(instruction, quote=True)}"></head>'
            "<body><main><h1>Publisher redirect</h1>"
            f'<p>If not redirected, <a href="{escape(target, quote=True)}">continue here</a>.</p>'
            "</main></body></html>"
        ).encode(),
        URL,
        content_type="text/html",
    )


def test_missing_metadata_content_does_not_consume_the_bounded_publisher_label_allowance():
    names = [f"Engineering {index}" for index in range(20)]
    html = (
        '<meta property="article:tag">' * 20
        + "".join(f'<meta property="article:tag" content="{name}">' for name in names)
        + '<meta property="article:tag" content="Excess label">'
    )

    assert labels(html) == names


@pytest.mark.parametrize("invalid_labels", [7, {"name": "Not a declared string label"}])
def test_scalar_structured_nodes_and_wrong_label_types_do_not_hide_visible_article_labels(
    invalid_labels,
):
    document = [
        None,
        False,
        12,
        "Not an article",
        {"@type": "Article", "keywords": invalid_labels, "articleSection": invalid_labels},
    ]
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(document)
        + "</script></head><body><article><h1>Database engineering</h1>"
        '<p>Publisher guidance.</p><a rel="tag" href="/tags/databases">Databases</a>'
        "</article></body></html>"
    )

    assert labels(html) == ["Databases"]


def test_empty_download_cannot_supply_article_labels():
    assert labels("") == []


def test_malformed_refresh_instruction_cannot_follow_an_otherwise_matching_visible_link():
    target = "https://publisher.example/full"
    result = redirect_page(target, refresh=f"now;url={target}")

    assert pages.publisher_redirect(result) is None


@pytest.mark.parametrize("target", ["javascript:alert(1)", URL])
def test_publisher_refresh_never_returns_a_script_target_or_its_current_page(target):
    assert pages.publisher_redirect(redirect_page(target)) is None
