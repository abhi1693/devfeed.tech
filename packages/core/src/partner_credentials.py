"""Write-only import credentials, authenticated and bound to one connection origin."""

import json

from cryptography.fernet import Fernet, InvalidToken

from devfeed_core.config import get_settings
from devfeed_core.partner_connectors import ConnectorConfig, connector_snapshot


class CredentialUnavailable(ValueError):
    """A safe diagnostic which never includes key material or upstream content."""


def credential_storage_available() -> bool:
    return get_settings().partner_credential_key is not None


def cipher() -> Fernet:
    key = get_settings().partner_credential_key
    if key is None:
        raise CredentialUnavailable("Secure import credentials are not configured")
    return Fernet(key.get_secret_value().encode("ascii"))


def credential_context(connection, config: ConnectorConfig) -> dict[str, str]:
    if connection.account_id is None:
        raise CredentialUnavailable("An account is required for connection credentials")
    return {
        "account_id": str(connection.account_id),
        "provider": connection.provider,
        "origin": config.base_url,
        "mode": config.auth.mode,
        "header": config.auth.header.lower(),
    }


def validate_credential(value: str) -> str:
    if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise CredentialUnavailable(
            "Import credential must contain 1 to 4096 visible ASCII characters"
        )
    return value


def encrypt_credential(connection, config: ConnectorConfig, secret: str) -> str:
    if config.auth.mode == "none":
        raise CredentialUnavailable("Select connection authentication before adding a credential")
    payload = {
        "version": 1,
        **credential_context(connection, config),
        "secret": validate_credential(secret),
    }
    return cipher().encrypt(json.dumps(payload, sort_keys=True).encode()).decode("ascii")


def connection_credential(connection, config=None) -> str | None:
    """Decrypt only for the configured owner, origin and authentication scheme.

    Workers call this after checking their generation fence, outside the database
    transaction. Neither the returned secret nor ciphertext belongs in job payloads.
    """
    config = ConnectorConfig.model_validate(config or connector_snapshot(connection))
    if config.auth.mode == "none":
        return None
    encrypted = connection.encrypted_credential
    if not encrypted:
        raise CredentialUnavailable("Import connection credential is missing")
    try:
        payload = json.loads(cipher().decrypt(encrypted.encode("ascii")))
        expected = {"version": 1, **credential_context(connection, config)}
        if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in expected.items()):
            raise ValueError("Credential binding does not match")
        return validate_credential(payload["secret"])
    except (InvalidToken, ValueError, UnicodeError, KeyError, TypeError):
        raise CredentialUnavailable(
            "Import credential is unavailable; replace it before syncing"
        ) from None
