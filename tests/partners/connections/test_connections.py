"""Launch platform APIs cannot bypass independent product qualification."""

import pytest
from devfeed_core.partner_connections import (
    validate_qualification,
)
from devfeed_core.partner_connectors import ConnectorClient


def entry(**changes):
    return {
        "slug": "checker",
        "name": "API Checker",
        "url": "https://checker.example/",
        "productUrl": "https://nicklaunches.com/products/checker/",
        "pricing": None,
        "description": "Check breaking changes in OpenAPI specifications.",
        "categories": ["Developer Tools"],
        **changes,
    }


def test_live_contract_and_legacy_url_names():
    for record in [
        entry(),
        entry(url=entry()["productUrl"], productUrl=entry()["url"], pricing="one_time"),
    ]:
        products, cursor = ConnectorClient(
            "nick-launches", fetch=lambda _, record=record: {"items": [record], "nextCursor": None}
        ).discover_page()
        assert cursor is None
        product = ConnectorClient("nick-launches", fetch=lambda _, record=record: record).product(
            products[0]
        )
        assert product.product_url == "https://checker.example/"
        assert product.listing_url == "https://nicklaunches.com/products/checker/"
        assert product.evidence == []
        assert product.pricing == ("paid" if record["pricing"] else "unknown")


def test_category_filter_and_pagination():
    urls = []

    def fetch(url):
        urls.append(url)
        return {
            "results": [entry(), entry(slug="consumer", categories=["AI"])],
            "nextCursor": "opaque",
        }

    products, cursor = ConnectorClient("nick-launches", fetch=fetch).discover_page("a&b")
    assert len(products) == 1 and cursor == "opaque"
    assert "cursor=a%26b" in urls[0]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": [], "nextCursor": 1},
    ],
)
def test_invalid_pages_do_not_become_empty_success(body):
    with pytest.raises(ValueError):
        ConnectorClient("nick-launches", fetch=lambda _: body).discover_page()


def test_qualification_requires_independent_quotes():
    page = {
        "final_url": "https://checker.example/",
        "text": "Detect breaking changes between OpenAPI specifications.",
    }
    answer = {
        "decision": "qualified",
        "reason": "Checks API compatibility in CI.",
        "technologies": ["OpenAPI"],
        "evidence": [
            {
                "url": page["final_url"],
                "quote": page["text"],
                "capability": "Check API compatibility in CI.",
            }
        ],
    }
    assert validate_qualification(answer, page).decision == "qualified"
    answer["evidence"][0]["quote"] = "Invented claims about OpenAPI support."
    with pytest.raises(ValueError):
        validate_qualification(answer, page)


def test_discovery_does_not_fetch_or_validate_individual_products():
    calls = []

    def fetch(url):
        calls.append(url)
        return {
            "items": [entry(description=""), {"slug": "broken", "categories": None}, "invalid"],
            "nextCursor": None,
        }

    candidates, cursor = ConnectorClient("nick-launches", fetch=fetch).discover_page()
    assert cursor is None and len(calls) == 1 and len(candidates) == 3
    assert candidates[0].external_id == "checker"
    assert candidates[1].external_id == "broken"
    assert candidates[2].external_id.startswith("invalid-")
    assert (
        ConnectorClient("nick-launches", fetch=lambda _: entry()).product(candidates[0]).name
        == "API Checker"
    )
    with pytest.raises(ValueError):
        ConnectorClient(
            "nick-launches", fetch=lambda _: pytest.fail("Invalid ID must not be fetched")
        ).product(candidates[2])


def test_product_endpoint_identity_must_match_discovery():
    candidates, _ = ConnectorClient(
        "nick-launches", fetch=lambda _: {"items": [entry()], "nextCursor": None}
    ).discover_page()
    with pytest.raises(ValueError, match="different product ID"):
        ConnectorClient("nick-launches", fetch=lambda _: entry(slug="other")).product(candidates[0])
