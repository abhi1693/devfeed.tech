"""Downloaded publisher metadata remains bounded and excludes hidden/nonarticle claims."""

import json

import pytest
from devfeed_aggregator import article_pages as pages
from devfeed_core.feeds.fetcher import FetchResult


def test_author_references_are_resolved_only_for_article_nodes_and_keep_unique_plain_names():
    document = json.dumps(
        {
            "@graph": [
                {"@type": "Organization", "name": "Site publisher", "author": "Not the author"},
                {"@id": "#writer", "@type": "Person", "name": "<b>Ada</b> Lovelace"},
                {"@type": "Article", "author": [None, "https://author.example/profile"]},
                {
                    "@type": "BlogPosting",
                    "author": [
                        {"@id": "#writer"},
                        "Ada Lovelace",
                        {"name": "Grace Hopper"},
                        {"name": ""},
                        {"name": 42},
                    ],
                },
            ]
        }
    )
    assert pages.structured_author([document]) == "Ada Lovelace; Grace Hopper"
    assert (
        pages.structured_author([json.dumps({"@type": "Organization", "author": "Site owner"})])
        is None
    )


def test_deep_untrusted_structured_metadata_cannot_hide_a_bounded_sibling_article():
    nested = {"@type": "Article", "author": "Hidden deep author"}
    for _ in range(16):
        nested = {"mainEntity": nested}
    document = json.dumps([nested, {"@type": "Article", "author": "Visible author"}])
    assert pages.structured_author([document]) == "Visible author"
    assert pages.structured_author([json.dumps(nested)]) is None


def test_metadata_from_templates_and_broken_category_links_cannot_supply_article_labels():
    html = """<html><head><template><meta name="keywords" content="Template only"></template>
      <meta name="keywords" content="Python, APIs"></head><body>
      <article><h1>Software API architecture</h1><ul>
      <li><a href="http://[broken/category/injected">Malformed category</a></li>
      <li><a href="/category/security">Security</a></li></ul></article></body></html>"""
    parser = pages.MetadataParser("https://publisher.example/post")
    parser.feed(html)
    assert pages.article_tags(html, parser) == ["Python", "APIs", "Security"]


def test_long_summary_ends_at_a_complete_sentence_when_it_fits_the_preview():
    sentence = "Software architecture decisions should explain the relevant engineering tradeoffs. "
    complete = (sentence * 4).strip()
    text = complete + " " + "Additional details without a sentence boundary " * 10
    assert len(text) > 500 and 250 < len(complete) < 499
    assert pages.excerpt(text) == complete


@pytest.mark.parametrize("body", [b"", b"   "])
def test_empty_download_does_not_invent_a_paywall_or_publisher_redirect(body):
    assert pages.paywall_signal(body.decode(), []) is None
    assert (
        pages.publisher_redirect(FetchResult(200, body, "https://publisher.example/post")) is None
    )
