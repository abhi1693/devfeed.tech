"""Short-lived gateway proofs identify callers without trusting public proxy headers."""

import hashlib
import hmac
import ipaddress
import re
import time

from devfeed_core.config import get_settings
from fastapi import Request

CALLER_HEADER = "X-DevFeed-Partner-Caller"
CALLER_PROOF_TTL = 60
CALLER_PROOF_CLOCK_SKEW = 5
_SIGNING_DOMAIN = b"devfeed:partner-caller:v1"
_IDENTITY_DOMAIN = b"devfeed:partner-caller:identity:v1"


def _normalized_ip(value: str) -> str | None:
    if "%" in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


def _proved_address(request: Request, key: str) -> str | None:
    proof = request.headers.get(CALLER_HEADER, "")
    if not proof or len(proof) > 256:
        return None
    payload, _, signature = proof.rpartition(".")
    version, _, remaining = payload.partition(".")
    issued, _, address = remaining.partition(".")
    if (
        version != "v1"
        or re.fullmatch(r"[0-9]{1,12}", issued) is None
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
    ):
        return None
    normalized = _normalized_ip(address)
    age = time.time() - int(issued)
    if normalized is None or not -CALLER_PROOF_CLOCK_SKEW <= age < CALLER_PROOF_TTL:
        return None
    signing_key = hmac.digest(key.encode(), _SIGNING_DOMAIN, "sha256")
    signed = f"{payload}\n{request.method}\n{request.url.path}"
    expected = hmac.new(signing_key, signed.encode(), hashlib.sha256).hexdigest()
    return normalized if hmac.compare_digest(expected, signature) else None


def partner_caller_key(request: Request) -> str:
    """Accept dedicated gateway proofs, otherwise use only the transport peer."""
    settings = get_settings()
    secret = settings.partner_caller_key
    address = _proved_address(request, secret.get_secret_value()) if secret else None
    if address is None:
        peer = request.client.host if request.client else "unknown"
        address = _normalized_ip(peer) or peer
    # The tracking key can hide peer addresses in Redis keys, but cannot authorize
    # gateway proofs. Never broaden the web service's receipt-signing permissions.
    secret = secret or settings.partner_tracking_key
    if secret:
        identity_key = hmac.digest(secret.get_secret_value().encode(), _IDENTITY_DOMAIN, "sha256")
        return hmac.new(identity_key, address.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(_IDENTITY_DOMAIN + b"\0" + address.encode()).hexdigest()
