"""Malformed connector input and unbound credentials fail before durable effects."""

import copy
import uuid

import pytest
from cryptography.fernet import Fernet
from devfeed_core import partner_ad_formats, partner_credentials
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.partner_connectors import ConnectorClient, ConnectorDiagnostic, included
from devfeed_core.partner_credentials import CredentialUnavailable, encrypt_credential
from devfeed_core.partner_tools import ProductInput
from devfeed_http.partner_connector_previews import public_diagnostic
from pydantic import SecretStr, ValidationError
from test_partner_connection_workflow_unit import connection
from test_partner_connectors import definition, entry
from test_partner_matching_workflow_unit import product
from test_partner_worker_identity_boundaries_unit import incoming
from test_source_readiness_unit import configuration


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


@pytest.mark.parametrize(
    "guard,diagnostic",
    [
        ("unassigned", "An account is required for connection credentials"),
        ("no-auth", "Select connection authentication before adding a credential"),
    ],
)
def test_encryption_refuses_unassigned_or_unauthenticated_connection_without_mutating_it(
    monkeypatch, guard, diagnostic
):
    configured = definition(auth={"mode": "none" if guard == "no-auth" else "bearer"})
    current = connection(
        account_id=None if guard == "unassigned" else uuid.uuid4(), encrypted_credential=None
    )
    settings = configuration(partner_credential_key=SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(partner_credentials, "get_settings", lambda: settings)
    before = copy.deepcopy((current.account_id, current.connector, current.encrypted_credential))
    with pytest.raises(CredentialUnavailable, match=f"^{diagnostic}$"):
        encrypt_credential(current, configured, "private-import-token")
    assert (current.account_id, current.connector, current.encrypted_credential) == before


@pytest.mark.parametrize("formats", [None, [], ["feed"], ["feed", "engagement"]])
def test_product_format_validation_preserves_inheritance_and_entitled_explicit_selections(formats):
    before = copy.deepcopy(formats)
    assert partner_ad_formats.validate_product_formats("platinum", formats) is None
    assert formats == before


@pytest.mark.parametrize("formats", [["feed", "feed"], ["unknown"]])
def test_product_format_validation_rejects_ambiguous_or_unknown_request_values(formats):
    with pytest.raises(ValueError, match="Choose unique product ad formats"):
        partner_ad_formats.validate_product_formats("platinum", formats)


def test_paid_format_requires_entitlement_but_prior_restriction_can_survive_downgrade():
    with pytest.raises(ValueError, match="requires Silver"):
        partner_ad_formats.validate_product_formats("bronze", ["engagement"])
    assert (
        partner_ad_formats.validate_product_formats(
            "bronze", ["engagement"], previous=["engagement"]
        )
        is None
    )


def test_transport_diagnostic_hides_private_exception_and_preserves_actionable_recovery():
    error = FeedError("private upstream endpoint and credential", reason="transport_error")
    assert (
        public_diagnostic(error, "fallback")
        == "Could not connect to the import API or the request timed out. Check API availability."
    )


def test_unrecognized_validation_field_uses_safe_preview_fallback_without_echoing_rejected_input():
    document = incoming(product()).model_dump(mode="json")
    document["pricing"] = "private upstream value"
    with pytest.raises(ValidationError) as error:
        ProductInput.model_validate(document)
    assert (
        public_diagnostic(error.value, "Could not normalize this imported product")
        == "Could not normalize this imported product"
    )
