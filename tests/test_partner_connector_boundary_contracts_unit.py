"""Malformed upstream structures and bounded previews fail with safe diagnostics."""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_connectors as connectors
from devfeed_core.partner_connectors import ConnectorClient, ProductCandidate
from pydantic import ValidationError
from test_partner_connectors import definition, entry


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
