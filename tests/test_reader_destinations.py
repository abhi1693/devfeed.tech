"""Reader return locations resolve into validated, single-encoded route state."""

import pytest
from devfeed_http.reader_destinations import (
    ReaderDestination,
    parse_reader_destination,
    serialize_reader_destination,
)


@pytest.mark.parametrize(
    ("wire", "record", "canonical"),
    [
        (
            "/topics/python/tutorials?sort=newest&q=100%25+%7E*%2B%2F",
            {
                "routeId": "topics-content",
                "parameters": {"slug": "python", "contentType": "tutorial"},
                "query": {"sort": "newest", "q": "100% ~*+/"},
            },
            "/topics/python/tutorials?sort=newest&q=100%25+%7E*%2B%2F",
        ),
        (
            "/tags/C%2B%2B/news?q=%E6%95%B0%E6%8D%AE%E5%BA%93",
            {
                "routeId": "tags-content",
                "parameters": {"slug": "C++", "contentType": "news"},
                "query": {"q": "数据库"},
            },
            "/tags/C%2B%2B/news?q=%E6%95%B0%E6%8D%AE%E5%BA%93",
        ),
        (
            "/search?q=%5Cd+C%3A%5CWindows",
            {"routeId": "search", "parameters": {}, "query": {"q": r"\d C:\Windows"}},
            "/search?q=%5Cd+C%3A%5CWindows",
        ),
        (
            "/tags/!~*'()%25?q=%252F%20test%20%7e",
            {
                "routeId": "tags-detail",
                "parameters": {"slug": "!~*'()%"},
                "query": {"q": "%2F test ~"},
            },
            "/tags/!~*'()%25?q=%252F+test+%7E",
        ),
        (
            "/settings/",
            {"routeId": "settings", "parameters": {"section": "profile"}, "query": {}},
            "/settings/profile",
        ),
        (
            "/search?q=%F0%9F%90%8D&content_types=article&section=articles&sort=relevance",
            {
                "routeId": "search",
                "parameters": {},
                "query": {
                    "q": "🐍",
                    "content_types": "article",
                    "section": "articles",
                    "sort": "relevance",
                },
            },
            "/search?q=%F0%9F%90%8D&content_types=article&section=articles&sort=relevance",
        ),
    ],
)
def test_resolve_semantic_parameters_and_canonical_encoding(wire, record, canonical):
    destination = parse_reader_destination(wire)
    assert isinstance(destination, ReaderDestination)
    assert destination.as_record() == record
    assert serialize_reader_destination(destination) == canonical
    assert serialize_reader_destination(record) == canonical


@pytest.mark.parametrize(
    "wire",
    [
        "https://devfeed.tech/latest",
        "//devfeed.tech/latest",
        "//evil.example/../latest",
        "/\\evil.example/latest",
        "/latest#fragment",
        "/latest#",
        "/latest\n",
        "/latest ",
        "/lat%65st",
        "/%74opics/rust",
        "/%2e/latest",
        "/%2e%2e/latest",
        "/topics/python/../news",
        "/topics/python/%2Fnews",
        "/topics/python/%252Fnews",
        "/topics//python",
        "/latest//",
        "/tags/%2F%2Fevil.example",
        "/tags/..",
        "/tags/%2e%2e",
        "/tags/%5Cevil",
        "/tags/%FF",
        "/tags/%ED%A0%80",
        "/tags/\ud800",
        "/search?q=\ud800",
        "/search?q=%FF",
        "/search?q=%ED%A0%80",
        "/search?q=%0d%0aLocation%3Aevil",
        "/search?q=%xx",
        "/search?q=one&%71=two",
        "/search?q=one&",
        "/search?q=one&&sort=newest",
        "/search?q",
        "/search?=one",
        "/search?next=https%3A%2F%2Fevil.example",
        "/search?section=unknown",
        "/search?sort=most_liked",
        "/search?content_types=article,news",
        "/search?content_types=unknown",
        "/search?date_from=0000-01-01",
        "/search?date_from=2026-02-29",
        "/search?date_from=2026-1-1",
        "/topics?offset=-1",
        "/topics?offset=1.2",
        "/topics?offset=1000001",
        "/latest?source_id=not-a-uuid",
        "/latest?sort=relevance",
        "/latest?content_type=unknown",
        "/search?q=" + "🐍" * 101,
        "/topics/" + "p" * 101,
        "/articles/" + "a" * 201,
        "/mcp/authorize?request=" + "a" * 42,
        "/mcp/authorize?request=" + "a" * 44,
        "/mcp/authorize?request=" + "a" * 43 + "&request=" + "b" * 43,
        "/login",
        "/my-feed",
    ],
)
def test_reject_ambiguous_unsafe_or_unsupported_destinations(wire):
    assert parse_reader_destination(wire) is None


@pytest.mark.parametrize(
    "record",
    [
        None,
        "/latest",
        [],
        {},
        {"routeId": "latest", "parameters": {}, "query": {}, "href": "//evil.example"},
        {"routeId": "latest", "parameters": {}, "query": {"next": "//evil.example"}},
        {"routeId": "latest", "parameters": {"slug": "ignored"}, "query": {}},
        {"routeId": "latest", "parameters": {}, "query": {"q": "bad\r\nvalue"}},
        {"routeId": "topics", "parameters": {}, "query": {"offset": "9" * 10000}},
        {"routeId": "topics", "parameters": {}, "query": {"offset": 2}},
        {"routeId": "article", "parameters": {"slug": "../../login"}, "query": {}},
        {"routeId": "tags-detail", "parameters": {"slug": r"C:\Windows"}, "query": {}},
        {
            "routeId": "topics-content",
            "parameters": {"slug": "python", "contentType": "tutorials"},
            "query": {},
        },
        {"routeId": "mcp-authorize", "parameters": {}, "query": {}},
        {"routeId": "unknown", "parameters": {}, "query": {}},
        {"routeId": "login", "parameters": {}, "query": {}},
    ],
)
def test_stored_destination_requires_exact_validated_structure(record):
    assert serialize_reader_destination(record) is None
