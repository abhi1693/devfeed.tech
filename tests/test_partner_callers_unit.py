"""Only authenticated gateway proofs can replace a partner request's transport peer."""

import hashlib
import hmac
import uuid

import pytest
from devfeed_api import partner_callers as callers
from devfeed_core.config import Settings, get_settings
from fastapi import Request
from pydantic import SecretStr, ValidationError

NOW = 1_800_000_000
KEY = "disposable-dedicated-caller-proof-key"
TRACKING_KEY = "disposable-different-partner-tracking-key"
PATH = "/v1/partner-placements/query"


def request(peer="203.0.113.10", *, headers=None, method="POST", path=PATH):
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": path,
            "root_path": "",
            "query_string": b"",
            "headers": [
                (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
            ],
            "client": (peer, 5000) if peer is not None else None,
            "server": ("api.example", 443),
        }
    )


def proof(address, *, issued=NOW, key=KEY, method="POST", path=PATH):
    payload = f"v1.{issued}.{address}"
    derived = hmac.digest(key.encode(), b"devfeed:partner-caller:v1", "sha256")
    signature = hmac.new(
        derived, f"{payload}\n{method}\n{path}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_caller_key", SecretStr(KEY))
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(TRACKING_KEY))
    monkeypatch.setattr(callers.time, "time", lambda: NOW)


def test_transport_peers_have_stable_distinct_opaque_keys():
    first = callers.partner_caller_key(request())
    assert callers.partner_caller_key(request()) == first
    assert callers.partner_caller_key(request("203.0.113.11")) != first
    assert len(first) == 64 and "203.0.113.10" not in first
    assert callers.partner_caller_key(request(None)) == callers.partner_caller_key(request(None))


@pytest.mark.parametrize(
    "left,right",
    [
        ("2001:db8:0:0:0:0:0:1", "2001:db8::1"),
        ("::ffff:203.0.113.10", "203.0.113.10"),
    ],
)
def test_equivalent_network_addresses_share_a_budget(left, right):
    assert callers.partner_caller_key(request(left)) == callers.partner_caller_key(request(right))


def test_public_forwarding_headers_cannot_change_the_caller():
    expected = callers.partner_caller_key(request())
    assert (
        callers.partner_caller_key(
            request(
                headers={
                    "X-Forwarded-For": "198.51.100.1, 192.0.2.1",
                    "Forwarded": 'for="[2001:db8::1]"',
                    "CF-Connecting-IP": "198.51.100.2",
                    "X-Real-IP": "198.51.100.3",
                }
            )
        )
        == expected
    )


def test_authenticated_gateway_proofs_separate_readers_behind_one_proxy():
    first = request("10.0.0.5", headers={callers.CALLER_HEADER: proof("203.0.113.10")})
    second = request("10.0.0.5", headers={callers.CALLER_HEADER: proof("203.0.113.11")})
    assert callers.partner_caller_key(first) == callers.partner_caller_key(request("203.0.113.10"))
    assert callers.partner_caller_key(second) == callers.partner_caller_key(request("203.0.113.11"))
    assert callers.partner_caller_key(first) != callers.partner_caller_key(second)


@pytest.mark.parametrize("issued", [NOW - 59, NOW, NOW + 5])
def test_current_gateway_proofs_and_small_clock_skew_are_accepted(issued):
    proxied = request(
        "10.0.0.5", headers={callers.CALLER_HEADER: proof("203.0.113.10", issued=issued)}
    )
    assert callers.partner_caller_key(proxied) == callers.partner_caller_key(request())


@pytest.mark.parametrize(
    "value",
    [
        proof("203.0.113.10", issued=NOW - 60),
        proof("203.0.113.10", issued=NOW + 6),
        proof("203.0.113.10", key=TRACKING_KEY),
        proof("203.0.113.10", method="GET"),
        proof("203.0.113.10", path="/v1/partner-tracking/events"),
        proof("203.0.113.10")[:-1] + "z",
        proof("not-an-address"),
        proof("fe80::1%eth0"),
        proof("203.0.113.10").replace("v1.", "v2.", 1),
        "x" * 257,
        "",
    ],
)
def test_invalid_gateway_proofs_fall_back_to_the_transport_peer(value):
    expected = callers.partner_caller_key(request("10.0.0.5"))
    assert (
        callers.partner_caller_key(request("10.0.0.5", headers={callers.CALLER_HEADER: value}))
        == expected
    )


def test_gateway_proofs_require_the_dedicated_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_caller_key", None)
    attempted = request(
        "10.0.0.5", headers={callers.CALLER_HEADER: proof("203.0.113.10", key=TRACKING_KEY)}
    )
    assert callers.partner_caller_key(attempted) == callers.partner_caller_key(request("10.0.0.5"))
    monkeypatch.setattr(get_settings(), "partner_tracking_key", None)
    assert callers.partner_caller_key(request()) == callers.partner_caller_key(request())
    assert callers.partner_caller_key(request()) != callers.partner_caller_key(request("192.0.2.1"))


@pytest.mark.parametrize("value", ["", " ", SecretStr("")])
def test_blank_caller_configuration_disables_proof_trust(value):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://example/example",
        redis_url="redis://example/0",
        partner_caller_key=value,
    )
    assert settings.partner_caller_key is None


def test_caller_configuration_requires_32_bytes_without_disclosing_the_key():
    arguments = dict(
        _env_file=None,
        database_url="postgresql://example/example",
        redis_url="redis://example/0",
    )
    short = uuid.uuid4().hex[:31]
    with pytest.raises(ValidationError) as error:
        Settings(**arguments, partner_caller_key=short)
    assert short not in str(error.value)
    assert Settings(**arguments, partner_caller_key="é" * 16).partner_caller_key is not None
