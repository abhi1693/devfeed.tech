"""Import tokens remain write-only and cannot move between accounts or origins."""

import json
import uuid
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest
from cryptography.fernet import Fernet
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerConnection
from devfeed_core.partner_connectors import ConnectorConfig, api_json
from devfeed_core.partner_credentials import (
    CredentialUnavailable,
    connection_credential,
    encrypt_credential,
)
from devfeed_partner_api.connections import PartnerConnectionCreate
from pydantic import ValidationError


@pytest.fixture
def token_connection(monkeypatch):
    monkeypatch.setenv("DEVFEED_PARTNER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    config = ConnectorConfig.model_validate(
        {
            "base_url": "https://catalog.example",
            "auth": {"source": "connection", "mode": "bearer"},
        }
    )
    connection = PartnerConnection(
        provider="account-import",
        account_id=uuid.uuid4(),
        connector=config.model_dump(mode="json"),
    )
    connection.encrypted_credential = encrypt_credential(connection, config, "private-test-token")
    return connection, config


def test_authenticated_encryption_keeps_token_out_of_connector(token_connection):
    connection, config = token_connection
    assert connection_credential(connection) == "private-test-token"
    assert "private-test-token" not in connection.encrypted_credential
    assert "private-test-token" not in json.dumps(connection.connector)
    assert config.auth.secret_ref == ""


@pytest.mark.parametrize("field", ["account_id", "provider", "origin", "mode", "header"])
def test_token_is_bound_to_account_connection_origin_and_auth(token_connection, field):
    connection, config = token_connection
    raw = config.model_dump(mode="json")
    if field == "account_id":
        connection.account_id = uuid.uuid4()
    elif field == "provider":
        connection.provider = "different-connection"
    elif field == "origin":
        raw["base_url"] = "https://other.example"
    elif field == "mode":
        raw["auth"]["mode"] = "api_key"
    else:
        raw["auth"]["header"] = "X-Another-Key"
    with pytest.raises(CredentialUnavailable, match="unavailable"):
        connection_credential(connection, raw)


def test_tampered_ciphertext_and_wrong_key_fail_closed(token_connection, monkeypatch):
    connection, _ = token_connection
    original = connection.encrypted_credential
    connection.encrypted_credential = "invalid-ciphertext"
    with pytest.raises(CredentialUnavailable):
        connection_credential(connection)
    connection.encrypted_credential = original
    monkeypatch.setenv("DEVFEED_PARTNER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(CredentialUnavailable):
        connection_credential(connection)


def test_missing_key_has_a_safe_error(token_connection, monkeypatch):
    connection, config = token_connection
    monkeypatch.setattr(
        "devfeed_core.partner_credentials.get_settings",
        lambda: SimpleNamespace(partner_credential_key=None),
    )
    with pytest.raises(CredentialUnavailable, match="not configured"):
        encrypt_credential(connection, config, "replacement")
    with pytest.raises(CredentialUnavailable, match="unavailable"):
        connection_credential(connection)


@pytest.mark.parametrize("token", ["", "line\nbreak", " space", "x" * 4097, "nonascii-\u00e9"])
def test_invalid_token_never_reaches_http_headers(token_connection, token):
    connection, config = token_connection
    with pytest.raises(CredentialUnavailable):
        encrypt_credential(connection, config, token)


def test_connection_token_is_only_passed_as_an_origin_header(token_connection, monkeypatch):
    _, config = token_connection
    requests = []

    def fetch(url, *args, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(body=b'{"items": [], "nextCursor": null}')

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    result = api_json(
        "account-import",
        config,
        "https://catalog.example/products",
        credential="private-test-token",
    )
    assert result["items"] == []
    assert requests[0][1]["origin_headers"] == {"Authorization": "Bearer private-test-token"}
    with pytest.raises(ValueError, match="origin"):
        api_json(
            "account-import",
            config,
            "https://foreign.example/products",
            credential="private-test-token",
        )
    assert len(requests) == 1


def test_partner_contract_does_not_allow_server_secret_reference():
    with pytest.raises(ValidationError):
        PartnerConnectionCreate.model_validate(
            {
                "name": "Catalog",
                "connector": {
                    "base_url": "https://catalog.example",
                    "auth": {
                        "mode": "bearer",
                        "secret_ref": "DEVFEED_PARTNER_SECRET_OTHER_ACCOUNT",
                    },
                },
            }
        )
    body = PartnerConnectionCreate.model_validate(
        {"name": "Catalog", "connector": {"base_url": "https://catalog.example"}}
    )
    assert body.connector.max_pages == 100
    assert body.sync_interval_minutes == 360


@pytest.mark.parametrize(
    "url",
    [
        "http://catalog.example",
        "https://127.0.0.1",
        "https://localhost",
        "https://169.254.169.254",
        "https://[::1]",
        "https://user:password@catalog.example",
    ],
)
def test_partner_connector_rejects_unsafe_origins(url):
    with pytest.raises(ValidationError):
        PartnerConnectionCreate.model_validate({"name": "Catalog", "connector": {"base_url": url}})


@pytest.mark.parametrize("auth_mode", ["bearer", "api_key"])
@pytest.mark.parametrize(
    "encoding", ["literal", "url", "url_lower", "url_mixed", "form", "json_escaped"]
)
def test_credential_echoes_are_redacted_before_discovered_records_are_persisted(
    monkeypatch, auth_mode, encoding
):
    import re

    # Environment credentials can contain spaces, which distinguish form encoding
    # from percent encoding. Account-stored tokens are restricted to visible ASCII.
    secret = "unit Token/A+b?c=d&more"
    monkeypatch.setenv("DEVFEED_PARTNER_SECRET_UNIT_ECHO_TOKEN", secret)
    config = ConnectorConfig.model_validate(
        {
            "base_url": "https://catalog.example",
            "auth": {"mode": auth_mode, "secret_ref": "DEVFEED_PARTNER_SECRET_UNIT_ECHO_TOKEN"},
        }
    )
    encoded = secret
    if encoding.startswith("url"):
        encoded = quote(secret, safe="")
        if encoding == "url_lower":
            encoded = re.sub(r"%[0-9A-F]{2}", lambda match: match[0].lower(), encoded)
        elif encoding == "url_mixed":
            lower = iter([False, True] * len(encoded))
            encoded = re.sub(
                r"%[0-9A-F]{2}",
                lambda match: match[0].lower() if next(lower) else match[0],
                encoded,
            )
    elif encoding == "form":
        encoded = quote_plus(secret, safe="")
    body = json.dumps(
        {
            "items": [{"echo": "prefix " + encoded + " suffix", "nested": [{encoded: encoded}]}],
            "unchanged": "Unit token/a+B?C=D&more",
        }
    )
    if encoding == "json_escaped":
        body = body.replace("unit Token/", "\\u0075nit Token\\/")
    monkeypatch.setattr(
        "devfeed_core.partner_connectors._fetch",
        lambda *args, **kwargs: SimpleNamespace(body=body.encode()),
    )
    result = api_json("unit-echo", config, "https://catalog.example/products")
    assert result["items"] == [
        {"echo": "prefix [redacted] suffix", "nested": [{"[redacted]": "[redacted]"}]}
    ]
    assert result["unchanged"] == "Unit token/a+B?C=D&more"
