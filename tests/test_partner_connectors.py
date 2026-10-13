"""Configurable REST mappings, bounded pagination and credential isolation."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_core import partner_connectors as connectors
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.partner_connections import ProductCandidate


def definition(**changes):
    return connectors.ConnectorConfig.model_validate(
        {
            "base_url": "https://api.platform.example",
            "items_paths": ["data.products"],
            "detail_path": "/products/{id}",
            "detail_root": "data.product",
            "fields": {
                "external_id": ["identity.id"],
                "name": ["title"],
                "product_url": ["website"],
                "description": ["summary"],
            },
            "listing_url_template": "https://platform.example/products/{id}",
            "pagination": {"mode": "none"},
            **changes,
        }
    )


def entry(identity="tool"):
    return {
        "identity": {"id": identity},
        "title": "API Checker",
        "website": "https://checker.example",
        "summary": "Check API compatibility before deployment.",
        "technologies": ["Unverified claim"],
    }


def test_nested_mapping_and_detail_identity_keep_ai_independent():
    config = definition()
    candidates, cursor = connectors.discover_page(
        "platform", config, fetch=lambda _: {"data": {"products": [entry("a/b")]}}
    )
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": {"product": entry("a/b")}}

    product = connectors.mapped_product(
        "platform", config, ProductCandidate.model_validate(candidates[0]), fetch=fetch
    )
    assert cursor is None and calls == ["https://api.platform.example/products/a%2Fb"]
    assert product.provider == "platform" and product.external_id == "a/b"
    assert product.listing_url == "https://platform.example/products/a%2Fb"
    assert product.evidence == product.technologies == []
    with pytest.raises(ValueError, match="different product identity"):
        connectors.mapped_product(
            "platform",
            config,
            ProductCandidate.model_validate(candidates[0]),
            fetch=lambda _: {"data": {"product": entry("someone-else")}},
        )


@pytest.mark.parametrize(
    "mode,parameter,start,next_position",
    [("page", "page", 1, "2"), ("page", "page", 0, "1"), ("offset", "offset", 0, "2")],
)
def test_page_and_offset_use_total_and_stop(mode, parameter, start, next_position):
    config = definition(
        pagination={
            "mode": mode,
            "parameter": parameter,
            "page_size": 2,
            "start": start,
            "total_path": "data.total",
        }
    )
    calls = []

    def fetch(url):
        calls.append(parse_qs(urlsplit(url).query))
        return {"data": {"products": [entry("one"), entry("two")], "total": 3}}

    _, cursor = connectors.discover_page("platform", config, fetch=fetch)
    assert cursor == next_position and calls[0][parameter] == [str(start)]
    _, cursor = connectors.discover_page(
        "platform",
        config,
        cursor,
        fetch=lambda _: {"data": {"products": [entry("three")], "total": 3}},
    )
    assert cursor is None


@pytest.mark.parametrize("mode", ["none", "next_url"])
def test_explicit_null_pagination_sends_only_configured_query_parameters(monkeypatch, mode):
    config = definition(
        list_path="/catalog?version=2",
        parameters={"tenant": "ours"},
        pagination={"mode": mode, "parameter": None, "size_parameter": None, "next_path": "next"},
    )
    calls = []
    body = {"data": {"products": [entry()]}, "next": None}

    def fetch(url):
        calls.append(url)
        return body

    candidates, cursor = connectors.discover_page("custom", config, fetch=fetch)
    monkeypatch.setattr(connectors, "api_json", lambda provider, parsed, url, **kwargs: fetch(url))
    assert connectors.response_preview("custom", config)["data"] == body
    assert len(candidates) == 1 and cursor is None
    assert calls == ["https://api.platform.example/catalog?version=2&tenant=ours"] * 2
    # Null is a persisted choice, not an omitted field that restores legacy defaults.
    assert (
        connectors.ConnectorConfig.model_validate(config.model_dump()).pagination.parameter is None
    )
    assert config.pagination.size_parameter is None


@pytest.mark.parametrize("mode", ["cursor", "page", "offset"])
def test_paginated_modes_require_an_explicit_or_legacy_parameter(mode):
    with pytest.raises(ValueError, match="requires a request parameter"):
        definition(pagination={"mode": mode, "parameter": None, "size_parameter": None})
    assert definition(pagination={"mode": mode}).pagination.parameter == "cursor"


@pytest.mark.parametrize(
    "mode,parameter", [("cursor", "after"), ("page", "page"), ("offset", "skip")]
)
def test_nullable_size_parameter_preserves_configured_pagination(monkeypatch, mode, parameter):
    config = definition(
        pagination={
            "mode": mode,
            "parameter": parameter,
            "size_parameter": None,
            "next_path": "next",
        }
    )
    calls = []
    body = {"data": {"products": []}, "next": None}

    def fetch(url):
        calls.append(parse_qs(urlsplit(url).query))
        return body

    connectors.discover_page("custom", config, "token" if mode == "cursor" else None, fetch=fetch)
    monkeypatch.setattr(connectors, "api_json", lambda provider, parsed, url, **kwargs: fetch(url))
    connectors.response_preview("custom", config)
    position = "token" if mode == "cursor" else "1" if mode == "page" else "0"
    assert calls[0] == {parameter: [position]}
    assert calls[1] == ({} if mode == "cursor" else {parameter: [position]})


@pytest.mark.parametrize("provider", ["nick-launches", "shipyardhq"])
def test_named_presets_keep_legacy_pagination_defaults(provider):
    pagination = connectors.ConnectorConfig.model_validate(
        connectors.CONNECTOR_PRESETS[provider]
    ).pagination
    assert (
        pagination.mode,
        pagination.parameter,
        pagination.size_parameter,
        pagination.page_size,
    ) == ("cursor", "cursor", "limit", 10)


def test_omitted_size_parameter_retains_existing_limit_request(monkeypatch):
    config = definition(pagination={"mode": "none"})
    urls = []
    body = {"data": {"products": []}}
    connectors.discover_page("legacy", config, fetch=lambda url: urls.append(url) or body)
    monkeypatch.setattr(
        connectors, "api_json", lambda provider, parsed, url, **kwargs: urls.append(url) or body
    )
    connectors.response_preview("legacy", config)
    assert urls == ["https://api.platform.example/products?limit=10"] * 2


def test_null_parameters_do_not_conflict_but_identical_named_parameters_do():
    assert definition(pagination={"mode": "none", "parameter": None, "size_parameter": None})
    with pytest.raises(ValueError, match="parameters must differ"):
        definition(pagination={"mode": "none", "parameter": "same", "size_parameter": "same"})
    with pytest.raises(ValueError):
        definition(pagination={"mode": "none", "size_parameter": ""})


def test_cursor_tokens_and_next_urls_cannot_escape_origin():
    config = definition(
        pagination={"mode": "cursor", "parameter": "after", "next_path": "data.next"}
    )
    calls = []
    _, cursor = connectors.discover_page(
        "platform",
        config,
        "a&b",
        fetch=lambda url: calls.append(url) or {"data": {"products": [], "next": None}},
    )
    assert cursor is None and parse_qs(urlsplit(calls[0]).query)["after"] == ["a&b"]
    config.pagination.mode = "next_url"
    for target in [
        "https://evil.example/products",
        "http://api.platform.example/products",
        "https://127.0.0.1/products",
    ]:
        with pytest.raises(ValueError):
            connectors.discover_page(
                "platform",
                config,
                fetch=lambda _, target=target: {"data": {"products": [entry()], "next": target}},
            )
    _, cursor = connectors.discover_page(
        "platform",
        config,
        fetch=lambda _: {"data": {"products": [entry()], "next": "/products?signature=x%2Fy"}},
    )
    calls.clear()
    connectors.discover_page(
        "platform",
        config,
        cursor,
        fetch=lambda url: calls.append(url) or {"data": {"products": [], "next": None}},
    )
    assert calls == ["https://api.platform.example/products?signature=x%2Fy"]


def test_filters_and_missing_pages_fail_explicitly():
    config = definition(
        filters=[{"path": "status", "values": ["approved"], "include_missing": False}]
    )
    candidates, _ = connectors.discover_page(
        "platform",
        config,
        fetch=lambda _: {
            "data": {
                "products": [
                    entry(),
                    {**entry("yes"), "status": "Approved"},
                    {**entry("no"), "status": "rejected"},
                ]
            }
        },
    )
    assert [candidate["external_id"] for candidate in candidates] == ["yes"]
    with pytest.raises(ValueError):
        connectors.discover_page("platform", config, fetch=lambda _: {"unexpected": []})
    config.pagination.mode = "page"
    config.pagination.has_more_path = "more"
    with pytest.raises(ValueError, match="without products"):
        connectors.discover_page(
            "platform", config, fetch=lambda _: {"data": {"products": []}, "more": True}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://api.platform.example"},
        {"base_url": "https://localhost"},
        {"base_url": "https://192.168.1.101"},
        {"base_url": "https://user:password@api.platform.example"},
        {"list_path": "//evil.example/products"},
        {"detail_path": "/products/{other}"},
        {"fields": {"name": ["__import__('os')"]}},
        {"max_pages": 1001},
        {"auth": {"mode": "bearer", "secret_ref": "actual-secret"}},
        {
            "auth": {
                "mode": "api_key",
                "secret_ref": "DEVFEED_PARTNER_SECRET_TEST",
                "header": "Host",
            }
        },
    ],
)
def test_definitions_are_bounded_public_data(changes):
    with pytest.raises(ValueError):
        definition(**changes)


def test_auth_uses_env_reference_shared_budget_and_blocks_redirect(monkeypatch):
    config = definition(auth={"mode": "bearer", "secret_ref": "DEVFEED_PARTNER_SECRET_TEST"})
    monkeypatch.setenv("DEVFEED_PARTNER_SECRET_TEST", "test-token")
    budgets = []
    from contextlib import nullcontext

    monkeypatch.setattr(connectors, "create_redis", lambda _: nullcontext(object()))
    monkeypatch.setattr(
        connectors, "consume_rate_limits", lambda _, items: budgets.extend(items) or 0
    )

    def fetch(url, *_args, **kwargs):
        assert kwargs["origin_headers"] == {"Authorization": "Bearer test-token"}
        kwargs["before_request"](url)
        with pytest.raises(ValueError):
            kwargs["before_request"]("https://other.example/products")
        return SimpleNamespace(body=b'{"items": []}')

    monkeypatch.setattr(connectors, "_fetch", fetch)
    assert connectors.api_json("platform", config, config.base_url + "/products") == {"items": []}
    assert len(budgets) == 1 and budgets[0].limit == 60
    assert "test-token" not in config.model_dump_json()
    monkeypatch.setattr(connectors, "consume_rate_limits", lambda *_, **kwargs: 42)
    with pytest.raises(FeedError) as error:
        connectors.api_json("platform", config, config.base_url + "/products")
    assert error.value.retry_after == 42
    monkeypatch.delenv("DEVFEED_PARTNER_SECRET_TEST")
    with pytest.raises(ValueError, match="credential"):
        connectors.api_json("platform", config, config.base_url + "/products")


def test_top_level_array_and_numeric_id_without_detail_request():
    config = definition(items_paths=[""], detail_path=None)
    candidates, _ = connectors.discover_page("platform", config, fetch=lambda _: [entry(42)])
    product = connectors.mapped_product(
        "platform", config, ProductCandidate.model_validate(candidates[0])
    )
    assert product.external_id == "42"


def test_next_url_relative_query_uses_current_endpoint():
    config = definition(
        list_path="/v2/products", pagination={"mode": "next_url", "next_path": "next"}
    )
    _, cursor = connectors.discover_page(
        "platform", config, fetch=lambda _: {"data": {"products": [entry()]}, "next": "?page=2"}
    )
    assert cursor == "https://api.platform.example/v2/products?page=2"


def test_raw_response_preview_is_mapping_independent_bounded_and_redacted(monkeypatch):
    config = definition(
        auth={"mode": "bearer", "secret_ref": "DEVFEED_PARTNER_SECRET_TEST"},
        items_paths=["unknown.path"],
    )
    monkeypatch.setenv("DEVFEED_PARTNER_SECRET_TEST", "sensitive-example")
    calls = []
    monkeypatch.setattr(
        connectors,
        "api_json",
        lambda provider, config, url, **kwargs: (
            calls.append(url)
            or {
                "results": [{"id": index, "description": "x" * 2000} for index in range(20)],
                "apiKey": "private-key",
                "echo": "Bearer sensitive-example",
                "next": None,
            }
        ),
    )
    response = connectors.response_preview("platform", config)
    assert len(calls) == 1 and response["sampled"]
    assert len(response["data"]["results"]) == 3
    assert len(response["data"]["results"][0]["description"]) == 1000
    assert response["data"]["apiKey"] == "[redacted]"
    assert response["data"]["echo"] == "Bearer [redacted]"
    assert response["data"]["next"] is None


@pytest.mark.parametrize(
    "slug,website",
    [("cronwatch", "https://cronwatch.dev"), ("the-audio-stuff", "https://theaudiostuff.com")],
)
def test_shipyard_preset_keeps_official_website_distinct_from_platform_listing(slug, website):
    from devfeed_core.partner_connections import read_partner_page, read_partner_product

    record = {
        "id": f"prod_{slug}",
        "name": slug,
        "url": f"https://shipyardhq.dev/products/{slug}",
        "productUrl": website,
        "description": "Official product description",
        "pricing": {"model": "subscription"},
    }
    candidates, cursor = read_partner_page(
        "shipyardhq", fetch=lambda _: {"items": [record], "nextCursor": None}
    )
    product = read_partner_product("shipyardhq", candidates[0])
    assert cursor is None
    assert product.product_url == website + "/"
    assert product.listing_url == record["url"]
    assert product.pricing == "paid" and product.attribution == "Via ShipyardHQ"
    assert product.technologies == product.evidence == []


@pytest.mark.parametrize("wrong_role", ["productUrl", "url"])
def test_shipyard_platform_host_guard_rejects_reversed_url_roles(wrong_role):
    record = {
        "id": "prod_cronwatch",
        "name": "CronWatch",
        "url": "https://shipyardhq.dev/products/cronwatch",
        "productUrl": "https://cronwatch.dev/",
        "description": "Cron monitoring",
    }
    record[wrong_role] = record["url" if wrong_role == "productUrl" else "productUrl"]
    with pytest.raises(ValueError, match="website or platform listing is missing"):
        connectors.mapped_product(
            "shipyardhq",
            connectors.SHIPYARD_CONNECTOR,
            ProductCandidate(external_id=record["id"], data=record),
        )


def test_connector_presets_are_creation_defaults_and_never_overwrite_saved_custom_mapping():
    connection = SimpleNamespace(provider="shipyardhq", connector={})
    default = connectors.connector_snapshot(connection)
    assert default["auth"] == {
        "mode": "bearer",
        "source": "environment",
        "secret_ref": "DEVFEED_PARTNER_SECRET_PRODUCTS_API_TOKEN",
        "header": "X-API-Key",
    }
    assert default["fields"]["product_url"] == ["productUrl"]
    custom = {**default, "fields": {**default["fields"], "product_url": ["custom.website"]}}
    connection.connector = custom
    assert connectors.connector_snapshot(connection) == custom
