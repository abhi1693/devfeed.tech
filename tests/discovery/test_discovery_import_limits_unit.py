"""Publisher imports bound unique identities while retaining valid duplicate hints."""

import json

import pytest
from devfeed_core.discovery_import import parse_import


def import_body(urls, format):
    if format == "json":
        return json.dumps([{"homepage_url": url} for url in urls]).encode()
    if format == "opml":
        return (
            "<opml><body>"
            + "".join(f'<outline htmlUrl="{url}"/>' for url in urls)
            + "</body></opml>"
        ).encode()
    if format == "markdown":
        return "\n".join(f"[Publication]({url})" for url in urls).encode()
    return "\n".join(urls).encode()


@pytest.mark.parametrize("format", ["json", "opml", "markdown", "urls"])
def test_import_limit_applies_to_unique_publishers_and_accepts_the_documented_boundary(format):
    urls = [f"https://publisher.example/publication/{index}" for index in range(1000)]
    hints = parse_import(import_body(urls, format), format)
    assert [hint.homepage for hint in hints] == urls
    with pytest.raises(ValueError, match="1,000 unique publisher entries"):
        parse_import(import_body([*urls, "https://publisher.example/one-too-many"], format), format)


@pytest.mark.parametrize("format", ["json", "opml", "markdown", "urls"])
def test_repeated_hints_do_not_consume_additional_publisher_capacity(format):
    hints = parse_import(import_body(["https://publisher.example/blog"] * 1001, format), format)
    assert len(hints) == 1 and hints[0].homepage == "https://publisher.example/blog"


@pytest.mark.parametrize(
    "body,format,message",
    [
        (b"https://publisher.example", "csv", "Unsupported discovery import format"),
        (b"<body/>", "opml", "Expected an OPML document"),
        (b"{}", "json", "Expected at most 1,000 publisher objects"),
        (b"[null]", "json", "Each publisher requires homepage_url"),
        (b"[{}]", "json", "Each publisher requires homepage_url"),
    ],
)
def test_unsupported_or_misshapen_imports_fail_with_an_actionable_error(body, format, message):
    with pytest.raises(ValueError, match=message):
        parse_import(body, format)


def test_opml_group_headings_without_a_publisher_are_skipped():
    body = (
        b'<opml><body><outline text="Software publishers">'
        b'<outline htmlUrl="https://publisher.example/blog" title="Publication"/>'
        b"</outline></body></opml>"
    )
    hints = parse_import(body, "opml")
    assert len(hints) == 1
    assert (hints[0].homepage, hints[0].name, hints[0].feed_hint) == (
        "https://publisher.example/blog",
        "Publication",
        None,
    )
