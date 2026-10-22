"""Provider-controlled callback and JWT inputs must bind one verified identity."""

import hashlib
from base64 import urlsafe_b64encode
from unittest.mock import Mock

import jwt
import pytest
from devfeed_http import oidc, oidc_logout
from test_oidc_logout import signed as signed
from test_oidc_protocol_fail_closed_unit import identify
from test_oidc_protocol_fail_closed_unit import identity_provider as identity_provider


def test_callback_from_another_issuer_cannot_redeem_an_otherwise_present_code():
    with pytest.raises(oidc.OIDCError, match="^Authorization issuer mismatch$"):
        oidc.validate_callback_response(
            code="authorization-code",
            provider_error=None,
            issuer="https://another-issuer.example",
            expected_issuer="https://issuer.test",
        )


@pytest.mark.parametrize("keys", ["unique", "ambiguous", "different-kid"])
def test_optional_kid_still_requires_one_unambiguous_configured_signing_key(
    identity_provider, monkeypatch, keys
):
    value = identity_provider
    headers = {"kid": "unknown-key"} if keys == "different-kid" else {}
    token = jwt.encode(value.claims, value.key, algorithm="RS256", headers=headers)
    keyset = [value.public]
    if keys == "ambiguous":
        keyset.append({**value.public, "kid": "rotated-signing-key"})
    request = Mock(side_effect=[{"id_token": token}, {"keys": keyset}])
    monkeypatch.setattr(oidc, "request_json", request)
    if keys == "unique":
        output = identify(value)
        assert output["subject"] == value.claims["sub"]
        assert output["issuer"] == value.settings.oidc_issuer_url
    else:
        with pytest.raises(oidc.OIDCError, match="^Invalid ID token signing key$"):
            identify(value)
    assert [call.args[:2] for call in request.call_args_list] == [
        ("POST", value.metadata["token_endpoint"]),
        ("GET", value.metadata["jwks_uri"]),
    ]


def test_matching_signed_code_and_access_token_hashes_allow_the_bound_identity(identity_provider):
    value = identity_provider
    for claim, token in (("c_hash", "authorization-code"), ("at_hash", value.access_token)):
        digest = hashlib.sha256(token.encode()).digest()
        value.claims[claim] = urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()
    output = identify(value)
    assert output["subject"] == value.claims["sub"]
    assert output["id_claims"]["c_hash"] == value.claims["c_hash"]
    assert output["id_claims"]["at_hash"] == value.claims["at_hash"]


@pytest.mark.parametrize("token", ["header.payload", "header.payload.signature.extra"])
def test_logout_rejects_non_compact_jwt_shapes_before_fetching_provider_keys(
    signed, monkeypatch, token
):
    request = Mock()
    monkeypatch.setattr(oidc, "request_json", request)
    with pytest.raises(oidc.OIDCError, match="^Invalid logout token$"):
        oidc_logout.validate(signed.settings, signed.metadata, token)
    request.assert_not_called()
