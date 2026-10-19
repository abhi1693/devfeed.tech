"""Configurable REST mappings, bounded pagination and credential isolation."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_core import partner_connectors as connectors
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.partner_connectors import ProductCandidate


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
                "tagline": ["summary"],
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
    candidates, cursor = connectors.ConnectorClient(
        "platform", config, fetch=lambda _: {"data": {"products": [entry("a/b")]}}
    ).discover_page()
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": {"product": entry("a/b")}}

    product = connectors.ConnectorClient("platform", config, fetch=fetch).product(
        ProductCandidate.model_validate(candidates[0])
    )
    assert cursor is None and calls == ["https://api.platform.example/products/a%2Fb"]
    assert product.provider == "platform" and product.external_id == "a/b"
    assert product.listing_url == "https://platform.example/products/a%2Fb"
    assert product.evidence == product.technologies == []
    with pytest.raises(ValueError, match="different product ID"):
        connectors.ConnectorClient(
            "platform", config, fetch=lambda _: {"data": {"product": entry("someone-else")}}
        ).product(ProductCandidate.model_validate(candidates[0]))


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

    _, cursor = connectors.ConnectorClient("platform", config, fetch=fetch).discover_page()
    assert cursor == next_position and calls[0][parameter] == [str(start)]
    _, cursor = connectors.ConnectorClient(
        "platform", config, fetch=lambda _: {"data": {"products": [entry("three")], "total": 3}}
    ).discover_page(cursor)
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

    candidates, cursor = connectors.ConnectorClient("custom", config, fetch=fetch).discover_page()
    monkeypatch.setattr(connectors.ConnectorClient, "request", lambda self, url: fetch(url))
    assert connectors.ConnectorClient("custom", config).response_preview()["data"] == body
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

    connectors.ConnectorClient("custom", config, fetch=fetch).discover_page(
        "token" if mode == "cursor" else None
    )
    monkeypatch.setattr(connectors.ConnectorClient, "request", lambda self, url: fetch(url))
    connectors.ConnectorClient("custom", config).response_preview()
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
    connectors.ConnectorClient(
        "legacy", config, fetch=lambda url: urls.append(url) or body
    ).discover_page()
    monkeypatch.setattr(
        connectors.ConnectorClient, "request", lambda self, url: urls.append(url) or body
    )
    connectors.ConnectorClient("legacy", config).response_preview()
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
    _, cursor = connectors.ConnectorClient(
        "platform",
        config,
        fetch=lambda url: calls.append(url) or {"data": {"products": [], "next": None}},
    ).discover_page("a&b")
    assert cursor is None and parse_qs(urlsplit(calls[0]).query)["after"] == ["a&b"]
    config.pagination.mode = "next_url"
    for target in [
        "https://evil.example/products",
        "http://api.platform.example/products",
        "https://127.0.0.1/products",
    ]:
        with pytest.raises(ValueError):
            connectors.ConnectorClient(
                "platform",
                config,
                fetch=lambda _, target=target: {"data": {"products": [entry()], "next": target}},
            ).discover_page()
    _, cursor = connectors.ConnectorClient(
        "platform",
        config,
        fetch=lambda _: {"data": {"products": [entry()], "next": "/products?signature=x%2Fy"}},
    ).discover_page()
    calls.clear()
    connectors.ConnectorClient(
        "platform",
        config,
        fetch=lambda url: calls.append(url) or {"data": {"products": [], "next": None}},
    ).discover_page(cursor)
    assert calls == ["https://api.platform.example/products?signature=x%2Fy"]


def test_filters_and_missing_pages_fail_explicitly():
    config = definition(
        filters=[{"path": "status", "values": ["approved"], "include_missing": False}]
    )
    candidates, _ = connectors.ConnectorClient(
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
    ).discover_page()
    assert [candidate.external_id for candidate in candidates] == ["yes"]
    with pytest.raises(ValueError):
        connectors.ConnectorClient(
            "platform", config, fetch=lambda _: {"unexpected": []}
        ).discover_page()
    config.pagination.mode = "page"
    config.pagination.has_more_path = "more"
    with pytest.raises(ValueError, match="without products"):
        connectors.ConnectorClient(
            "platform", config, fetch=lambda _: {"data": {"products": []}, "more": True}
        ).discover_page()


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://api.platform.example"},
        {"base_url": "https://localhost"},
        {"base_url": "https://192.168.1.10"},
        {"base_url": "https://user:password@api.platform.example"},
        {"list_path": "//evil.example/products"},
        {"detail_path": "/products/{other}"},
        {"fields": {"name": ["__import__('os')"]}},
        {"max_pages": 0},
        {"max_pages": 1.5},
        {"max_pages": True},
        {
            "auth": {
                "mode": "api_key",
                "header": "Host",
            }
        },
    ],
)
def test_definitions_are_bounded_public_data(changes):
    with pytest.raises(ValueError):
        definition(**changes)


def test_auth_uses_explicit_credential_shared_budget_and_blocks_redirect(monkeypatch):
    config = definition(auth={"mode": "bearer"})
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
    assert connectors.ConnectorClient("platform", config, credential="test-token").request(
        config.base_url + "/products"
    ) == {"items": []}
    assert len(budgets) == 1 and budgets[0].limit == 60
    assert "test-token" not in config.model_dump_json()
    monkeypatch.setattr(connectors, "consume_rate_limits", lambda *_, **kwargs: 42)
    with pytest.raises(FeedError) as error:
        connectors.ConnectorClient("platform", config, credential="test-token").request(
            config.base_url + "/products"
        )
    assert error.value.retry_after == 42
    with pytest.raises(ValueError, match="credential"):
        connectors.ConnectorClient("platform", config).request(config.base_url + "/products")


def test_top_level_array_and_numeric_id_without_detail_request():
    config = definition(items_paths=[""], detail_path=None)
    candidates, _ = connectors.ConnectorClient(
        "platform", config, fetch=lambda _: [entry(42)]
    ).discover_page()
    product = connectors.ConnectorClient("platform", config).product(
        ProductCandidate.model_validate(candidates[0])
    )
    assert product.external_id == "42"


def test_next_url_relative_query_uses_current_endpoint():
    config = definition(
        list_path="/v2/products", pagination={"mode": "next_url", "next_path": "next"}
    )
    _, cursor = connectors.ConnectorClient(
        "platform", config, fetch=lambda _: {"data": {"products": [entry()]}, "next": "?page=2"}
    ).discover_page()
    assert cursor == "https://api.platform.example/v2/products?page=2"


def test_raw_response_preview_is_mapping_independent_bounded_and_redacted(monkeypatch):
    config = definition(
        auth={"mode": "bearer"},
        items_paths=["unknown.path"],
    )
    calls = []
    monkeypatch.setattr(
        connectors.ConnectorClient,
        "request",
        lambda self, url: (
            calls.append(url)
            or {
                "results": [{"id": index, "description": "x" * 2000} for index in range(20)],
                "apiKey": "private-key",
                "echo": "Bearer sensitive-example",
                "next": None,
            }
        ),
    )
    response = connectors.ConnectorClient(
        "platform", config, credential="sensitive-example"
    ).response_preview()
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
    from devfeed_core.partner_connectors import ConnectorClient

    record = {
        "id": f"prod_{slug}",
        "name": slug,
        "url": f"https://shipyardhq.dev/products/{slug}",
        "productUrl": website,
        "description": "Official product description",
        "pricing": {"model": "subscription"},
    }
    candidates, cursor = ConnectorClient(
        "shipyardhq", fetch=lambda _: {"items": [record], "nextCursor": None}
    ).discover_page()
    product = ConnectorClient("shipyardhq").product(candidates[0])
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
    with pytest.raises(ValueError, match="platform host"):
        connectors.ConnectorClient("shipyardhq", connectors.SHIPYARD_CONNECTOR).product(
            ProductCandidate(external_id=record["id"], data=record)
        )


def test_connector_presets_are_creation_defaults_and_never_overwrite_saved_custom_mapping():
    connection = SimpleNamespace(provider="shipyardhq", connector={})
    default = connectors.connector_snapshot(connection)
    assert default["auth"] == {
        "mode": "bearer",
        "header": "X-API-Key",
    }
    assert default["fields"]["product_url"] == ["productUrl"]
    custom = {**default, "fields": {**default["fields"], "product_url": ["custom.website"]}}
    connection.connector = custom
    assert connectors.connector_snapshot(connection) == custom


def test_one_client_keeps_validated_configuration_across_discovery_product_and_preview():
    raw = definition(detail_path=None).model_dump(mode="json")
    urls = []
    client = connectors.ConnectorClient(
        "account-catalog",
        raw,
        fetch=lambda url: urls.append(url) or {"data": {"products": [entry()]}},
    )
    # The caller's mutable request dictionary cannot change an already configured client.
    raw["base_url"] = "https://another-account.example"
    raw["parameters"]["injected"] = "later"
    candidates, cursor = client.discover_page()
    product = client.product(candidates[0])
    response = client.response_preview()
    assert cursor is None and product.provider == "account-catalog"
    assert candidates[0].normalized is False and product.technologies == []
    assert response["data"]["data"]["products"][0]["title"] == product.name
    assert urls == ["https://api.platform.example/products?limit=10"] * 2


def test_client_reuses_credential_deadline_and_budget_for_all_transport_methods(monkeypatch):
    from contextlib import nullcontext

    config = definition(auth={"mode": "bearer"})
    calls, budgets = [], []
    monkeypatch.setattr(connectors, "create_redis", lambda _: nullcontext(object()))
    monkeypatch.setattr(
        connectors, "consume_rate_limits", lambda _, values: budgets.extend(values) or 0
    )

    def fetch(url, *args, **kwargs):
        calls.append(kwargs)
        kwargs["before_request"](url)
        body = (
            {"data": {"product": entry()}}
            if url.endswith("/tool")
            else {"data": {"products": [entry()]}}
        )
        return SimpleNamespace(body=json.dumps(body).encode())

    monkeypatch.setattr(connectors, "_fetch", fetch)
    client = connectors.ConnectorClient(
        "owned-connection", config, credential="private-example", deadline=123.5
    )
    candidates, _ = client.discover_page()
    client.product(candidates[0])
    client.response_preview()
    assert len(calls) == len(budgets) == 3
    assert all(call["deadline"] == 123.5 for call in calls)
    assert all(
        call["origin_headers"] == {"Authorization": "Bearer private-example"} for call in calls
    )
    assert len({budget.key for budget in budgets}) == 1
    assert "private-example" not in budgets[0].key


def test_normalized_internal_candidate_never_calls_the_import_transport():
    client = connectors.ConnectorClient(
        "nick-launches", fetch=lambda _: pytest.fail("Unexpected request")
    )
    candidate = ProductCandidate(
        external_id="tool",
        normalized=True,
        data={
            "provider": "nick-launches",
            "external_id": "tool",
            "name": "Tool",
            "product_url": "https://tool.example/",
            "listing_url": "https://launch.example/tool",
            "tagline": "A bounded normalized test product.",
        },
    )
    assert client.product(candidate).name == "Tool"


def test_unknown_provider_requires_an_explicit_connector_definition():
    with pytest.raises(ValueError, match="configuration is missing"):
        connectors.ConnectorClient("unconfigured-platform")


@pytest.mark.parametrize("pages", [101, 1001, 1000000])
def test_max_pages_accepts_positive_integers_without_upper_bound(pages):
    from devfeed_partner_api.connections import PartnerConnectorConfig

    assert definition(max_pages=pages).max_pages == pages
    assert (
        PartnerConnectorConfig(base_url="https://catalog.example", max_pages=pages).max_pages
        == pages
    )


@pytest.mark.parametrize("pages", [0, -1, 1.5, True])
def test_partner_max_pages_requires_positive_whole_numbers(pages):
    from devfeed_partner_api.connections import PartnerConnectorConfig

    with pytest.raises(ValueError):
        PartnerConnectorConfig(base_url="https://catalog.example", max_pages=pages)
