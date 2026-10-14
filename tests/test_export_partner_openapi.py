import importlib.util
import json
from pathlib import Path

import pytest
from devfeed_core.partner_ad_formats import AUTOMATIC_AD_FORMATS
from devfeed_core.partner_connections import ProviderSlug
from devfeed_http.operation_policies import (
    DEFAULT_GATEWAY_POLICY,
    GATEWAY_POLICY_EXTENSION,
    GATEWAY_ROUTES_EXTENSION,
    GatewayPolicy,
)
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_partner_openapi", ROOT / "scripts/export_partner_openapi.py"
)
assert spec is not None and spec.loader is not None
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_partner_contract_export_is_current_and_needs_no_service_connections(monkeypatch):
    monkeypatch.setenv(
        "DEVFEED_DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1:1/contracts"
    )
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://127.0.0.1:1/0")

    def reject_connection(*args, **kwargs):
        raise AssertionError("Contract export attempted a network connection")

    monkeypatch.setattr("socket.socket.connect", reject_connection)
    document = exporter.build_contract()
    snapshot = json.loads((ROOT / "apps/partner/openapi.json").read_text())
    assert document == snapshot
    assert exporter.build_contract() == document
    assert document["x-devfeed-automatic-ad-formats"] == list(AUTOMATIC_AD_FORMATS)
    assert not any("/admin/" in path or "/user/" in path for path in document["paths"])


def test_authoritative_snapshot_keeps_input_and_output_models_distinct():
    document = exporter.build_contract()
    schemas = document["components"]["schemas"]
    assert "id" in schemas["AccountOut"]["required"]
    assert "allowed_promotion_kinds" in schemas["AccountOut"]["required"]
    assert "id" not in schemas["PartnerPreferencesInput"]["properties"]
    # Defaulted API fields stay optional in the authoritative OpenAPI. Only the
    # runtime response projection requires the complete serialized response.
    assert "outbound_destination" not in schemas["AccountOut"]["required"]
    assert schemas["AccountOut"]["properties"]["outbound_destination"]["default"] == "product_url"


def test_partner_gateway_exposes_registered_operations_with_endpoint_owned_limits():
    document = exporter.build_contract()
    operations = document[GATEWAY_ROUTES_EXTENSION]
    actual = {(operation["path"], operation["method"]): operation for operation in operations}
    documented = {
        (path, method.upper()): operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if path.startswith("/v1/partner/")
    }
    assert len(actual) == len(operations)
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert actual.keys() == documented.keys()
    assert ("/v1/partner/auth/callback", "GET") in actual
    assert ("/v1/partner/auth/logout", "POST") in actual
    assert not any(operation["method"] == "HEAD" for operation in operations)
    for path in ("/health/live", "/health/ready"):
        assert GATEWAY_POLICY_EXTENSION not in document["paths"][path]["get"]

    overrides = {
        "partnerProductCreate": GatewayPolicy(body_limit=65536),
        "partnerProductUpdate": GatewayPolicy(body_limit=65536),
        "partnerProductArchive": GatewayPolicy(body_limit=65536),
        "partnerProductAdImageUpload": GatewayPolicy(
            body_limit=5 * 1024 * 1024 + 65536, body_timeout_ms=10_000
        ),
    }
    for key, operation in actual.items():
        original = documented[key]
        assert operation["operationId"] == original["operationId"]
        policy = overrides.get(operation["operationId"], DEFAULT_GATEWAY_POLICY)
        assert operation["policy"] == original[GATEWAY_POLICY_EXTENSION]
        assert operation["policy"] == policy.model_dump(by_alias=True)


def test_partner_gateway_preserves_route_precedence_and_distinct_parameter_converters():
    operations = exporter.build_contract()[GATEWAY_ROUTES_EXTENSION]
    by_id = {operation["operationId"]: operation for operation in operations}
    ids = [operation["operationId"] for operation in operations]
    assert ids.index("partnerAccountsList") < ids.index("partnerAccountGet")
    assert ids.index("partnerConnectorPreview") < ids.index("partnerConnectionGet")
    assert ids.index("partnerConnectorResponse") < ids.index("partnerConnectionGet")

    detail = by_id["partnerProductGet"]
    upload = by_id["partnerProductAdImageUpload"]
    assert detail["path"] == "/v1/partner/accounts/{account_id}/products/{product_id}"
    for operation in (detail, upload):
        assert [parameter["name"] for parameter in operation["parameters"]] == [
            "account_id",
            "product_id",
        ]
        assert all(parameter["schema"]["format"] == "uuid" for parameter in operation["parameters"])
    assert [parameter["converter"] for parameter in detail["parameters"]] == ["str", "uuid"]
    assert [parameter["converter"] for parameter in upload["parameters"]] == ["str", "str"]

    provider_parameters = [
        parameter
        for operation in operations
        for parameter in operation["parameters"]
        if parameter["name"] == "provider"
    ]
    assert len(provider_parameters) == 9
    for parameter in provider_parameters:
        assert parameter["converter"] == "str"
        assert parameter["schema"] == {
            **TypeAdapter(ProviderSlug).json_schema(),
            "title": "Provider",
        }


@pytest.mark.parametrize("provider", ["custom-api", "account-abc-123", "a" * 200, "new"])
def test_provider_path_policy_accepts_domain_identifiers_without_ui_reserved_names(provider):
    assert TypeAdapter(ProviderSlug).validate_python(provider) == provider


@pytest.mark.parametrize(
    "provider", ["", "a" * 201, "Uppercase", "under_score", "a--b", "../other", " a", "a "]
)
def test_provider_path_policy_rejects_invalid_or_unbounded_identifiers(provider):
    with pytest.raises(ValidationError):
        TypeAdapter(ProviderSlug).validate_python(provider)
