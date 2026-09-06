import hashlib
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def validate_public_url(value: str) -> str:
    """Syntactic check; the fetcher's network backend also validates resolved IPs."""
    if len(value) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("URL is too long or contains whitespace/control characters")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("A public HTTP or HTTPS URL is required")
        if parts.username is not None or parts.password is not None:
            raise ValueError("Credentials in URLs are not allowed")
        if parts.port not in {None, 80, 443}:
            raise ValueError("Only ports 80 and 443 are allowed")
        host = parts.hostname.encode("idna").decode().lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Private hostnames are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
                raise ValueError("Nonstandard numeric IP addresses are not allowed") from None
            if "." not in host or "\\" in host or "%" in host:
                raise ValueError("A public hostname is required") from None
        else:
            if not address.is_global:
                raise ValueError("Private or reserved IP addresses are not allowed")
    except (UnicodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    port = parts.port
    netloc = f"[{host}]" if ":" in host else host
    if port and (parts.scheme, port) not in {("https", 443), ("http", 80)}:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def canonicalize_url(value: str) -> str:
    parts = urlsplit(validate_public_url(value))
    # Preserve the exact query when there is nothing to remove (signed URLs).
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMETERS
    ]
    query = parts.query if len(kept) == len(pairs) else urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
