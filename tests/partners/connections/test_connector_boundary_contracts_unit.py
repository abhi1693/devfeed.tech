"""Malformed upstream structures and bounded previews fail with safe diagnostics."""

import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_connectors as connectors
from devfeed_core.partner_connectors import (
    ConnectorClient,
    ConnectorDiagnostic,
    ProductCandidate,
    included,
)
from pydantic import ValidationError

from tests.partners.connections.test_connectors import definition, entry


@pytest.mark.parametrize(
    "changes",
    [
        {"pagination": {"mode": "page", "parameter": "page", "size_parameter": "page"}},
        {"parameters": {"query": "bad\nvalue"}},
        {"platform_hosts": ["invalid/hostname"]},
        {"listing_url_template": "https://launch.example/products/{other}"},
        {"filters": [{"path": "status", "values": ["   "]}]},
    ],
)
def test_connector_definition_rejects_ambiguous_or_unbounded_mapping_policy(changes):
    with pytest.raises(ValidationError):
        definition(**changes)


def test_mapped_fallback_supports_array_index_and_ignores_missing_empty_alternatives():
    payload = {"data": [{"label": "", "name": "API Checker"}]}
    assert (
        connectors.first_value(payload, ["missing", "data.0.label", "data.0.name"], required=True)
        == "API Checker"
    )
    assert connectors.value_at(payload, "data.0.name") == "API Checker"
    with pytest.raises(ValueError, match="Required mapped"):
        connectors.first_value(payload, ["data.9.name", "data.invalid"], required=True)


def test_missing_filtered_value_can_be_deliberately_included_without_accepting_explicit_mismatch():
    config = definition(filters=[{"path": "status", "values": ["live"], "include_missing": True}])
    assert connectors.included(config, {"status": None})
    assert not connectors.included(config, {"status": "archived"})


@pytest.mark.parametrize("cursor", ["not-a-number", "-1", "1000000001"])
def test_import_pagination_rejects_invalid_position_before_network_request(cursor):
    fetch = Mock()
    client = ConnectorClient(
        "platform", definition(pagination={"mode": "page", "parameter": "page"}), fetch=fetch
    )
    with pytest.raises(ValueError, match="Invalid pagination position"):
        client.discover_page(cursor)
    fetch.assert_not_called()


def test_opaque_cursor_size_is_bounded_before_any_upstream_request():
    client = ConnectorClient(
        "platform",
        definition(pagination={"mode": "cursor", "parameter": "cursor", "next_path": "next"}),
        fetch=Mock(),
    )
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        client.discover_page("x" * 2001)
    client.fetch.assert_not_called()


@pytest.mark.parametrize(
    "case", ["missing_next", "wrong_has_more", "boolean_total", "no_products_but_more"]
)
def test_discovery_refuses_inconsistent_upstream_pagination_instead_of_looping(case):
    body = {"data": {"products": [entry()]}}
    pagination = {"mode": "page", "parameter": "page", "page_size": 2}
    if case == "missing_next":
        pagination = {"mode": "cursor", "parameter": "cursor", "next_path": "missing"}
    elif case == "wrong_has_more":
        pagination["has_more_path"] = "more"
        body["more"] = "yes"
    elif case == "boolean_total":
        pagination["total_path"] = "total"
        body["total"] = True
    else:
        pagination["has_more_path"] = "more"
        body["data"]["products"], body["more"] = [], True
    client = ConnectorClient("platform", definition(pagination=pagination), fetch=lambda _: body)
    with pytest.raises(connectors.ConnectorDiagnostic):
        client.discover_page()


@pytest.mark.parametrize("case", ["missing_identity", "missing_detail_root"])
def test_product_normalization_reports_missing_mapping_without_inventing_facts(case):
    data = entry()
    if case == "missing_identity":
        data.pop("identity")
    client = ConnectorClient("platform", definition(), fetch=lambda _: {"unexpected": {}})
    with pytest.raises(connectors.ConnectorDiagnostic, match="ID paths|Detail response root"):
        client.product(ProductCandidate(external_id="tool", data=data))


def test_invalid_json_response_has_operator_safe_diagnostic(monkeypatch):
    fetch = Mock(return_value=NS(body=b"<html>upstream failure</html>"))
    monkeypatch.setattr(connectors, "_fetch", fetch)
    client = ConnectorClient("platform", definition())
    with pytest.raises(connectors.ConnectorDiagnostic, match="invalid JSON") as rejected:
        client.request("https://api.platform.example/products")
    assert "<html>" not in str(rejected.value)


def test_response_redaction_bounds_recursion_and_removes_numeric_secret_echo(monkeypatch):
    deep = "leaf"
    for _ in range(22):
        deep = {"nested": deep}
    body = {"numeric": 12345, "value": "Bearer 12345", "deep": deep}
    monkeypatch.setattr(connectors, "_fetch", lambda *_, **__: NS(body=json.dumps(body).encode()))
    client = ConnectorClient("platform", definition(auth={"mode": "bearer"}), credential="12345")
    output = client.request("https://api.platform.example/products")
    assert output["numeric"] == "[redacted]" and output["value"] == "Bearer [redacted]"
    assert "[omitted]" in json.dumps(output) and "12345" not in json.dumps(output)


def test_response_preview_bounds_total_nodes_depth_and_long_keys_without_exposing_secrets():
    deep = "leaf"
    for _ in range(10):
        deep = {"nested": deep}
    data = {
        "deep": deep,
        "k" * 210: "visible",
        "huge": {str(i): {str(j): "value" for j in range(40)} for i in range(40)},
    }
    client = ConnectorClient("platform", definition(), fetch=lambda _: data)
    output = client.response_preview()
    assert output["sampled"] and "k" * 200 in output["data"]
    assert "[omitted]" in json.dumps(output)
    assert len(json.dumps(output)) < len(json.dumps(data))


def test_optional_detail_endpoint_normalizes_the_list_record_without_an_additional_request():
    calls = []
    record = entry("api-checker")
    before = copy.deepcopy(record)

    def fetch(url):
        calls.append(url)
        return {"data": {"products": [record]}}

    client = ConnectorClient(
        "platform",
        definition(detail_path=None, pagination={"mode": "none", "size_parameter": None}),
        fetch=fetch,
    )
    candidates, cursor = client.discover_page()
    parsed = client.product(candidates[0])
    assert parsed.external_id == "api-checker" and parsed.name == "API Checker"
    assert parsed.product_url == "https://checker.example/"
    assert parsed.listing_url == "https://platform.example/products/api-checker"
    assert parsed.evidence == parsed.technologies == []
    assert cursor is None and calls == ["https://api.platform.example/products"]
    assert record == before


def test_list_endpoint_cannot_reuse_a_product_detail_placeholder():
    with pytest.raises(ValidationError, match="List endpoint cannot contain placeholders"):
        definition(list_path="/products/{id}")


@pytest.mark.parametrize("include_missing", [False, True])
def test_explicit_null_filter_value_obeys_missing_value_policy(include_missing):
    config = definition(
        filters=[{"path": "status", "values": ["live"], "include_missing": include_missing}]
    )
    assert included(config, {"status": None}) is include_missing
    assert not included(config, {"status": "archived"})


@pytest.mark.parametrize("body", [{}, {"data": {}}, {"data": {"products": None}}])
def test_missing_list_mapping_is_a_safe_diagnostic_instead_of_an_empty_success(body):
    client = ConnectorClient("platform", definition(), fetch=lambda _: body)
    with pytest.raises(ConnectorDiagnostic, match="Product list paths did not match"):
        client.discover_page()


@pytest.mark.parametrize("items", [{"id": "unexpected-object"}, [entry()] * 1001])
def test_list_mapping_requires_a_bounded_array_before_creating_candidates(items):
    client = ConnectorClient(
        "platform", definition(), fetch=lambda _: {"data": {"products": items}}
    )
    with pytest.raises(ConnectorDiagnostic, match="array of at most 1,000 products"):
        client.discover_page()


def test_discovered_record_without_identity_keeps_a_deterministic_diagnostic_candidate():
    record = entry()
    record.pop("identity")
    before = copy.deepcopy(record)
    client = ConnectorClient(
        "platform", definition(detail_path=None), fetch=lambda _: {"data": {"products": [record]}}
    )
    first, _ = client.discover_page()
    second, _ = client.discover_page()
    assert len(first) == 1 and first[0].external_id.startswith("invalid-")
    assert first[0].external_id == second[0].external_id
    with pytest.raises(ConnectorDiagnostic, match="Product ID paths did not match"):
        client.product(first[0])
    assert record == before


@pytest.mark.parametrize("invalid_id", [".", ".."])
def test_malformed_upstream_identity_cannot_become_a_detail_request(invalid_id):
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": {"products": [entry(invalid_id)]}}

    client = ConnectorClient("platform", definition(), fetch=fetch)
    candidates, _ = client.discover_page()
    assert candidates[0].external_id.startswith("invalid-")
    with pytest.raises(
        ConnectorDiagnostic, match="Imported product identity is missing or invalid"
    ):
        client.product(candidates[0])
    assert len(calls) == 1
