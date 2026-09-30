"""Concrete request URLs for logs, with all supplied query values retained."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

_HOST = re.compile(r"(?:[a-z0-9.-]+|\[[a-f0-9:.]+\])(?::[0-9]+)?\Z", re.IGNORECASE)


def request_log_fields(scope: Mapping[str, Any]) -> dict[str, str]:
    """Use the received request, not router templates or untrusted forwarding headers."""
    raw_path = scope.get("raw_path")
    path = (
        quote(raw_path.decode("ascii", "replace"), safe="/%:@!$&'()*+,;=-._~")
        if isinstance(raw_path, bytes)
        else quote(scope.get("path", "/"), safe="/:@!$&'()*+,;=-._~")
    )
    host = next(
        (value.decode("latin-1") for key, value in scope.get("headers", []) if key == b"host"),
        "",
    )
    scheme = scope.get("scheme", "http")
    if not _HOST.fullmatch(host):
        server = scope.get("server")
        if server:
            hostname, port = server
            hostname = f"[{hostname}]" if ":" in hostname else hostname
            host = (
                hostname
                if port in {None, {"http": 80, "https": 443}.get(scheme)}
                else f"{hostname}:{port}"
            )
        else:
            host = ""
    query = scope.get("query_string", b"").decode("utf-8", "replace")
    url = f"{scheme}://{host}{path}" if host else path
    return {
        "method": scope["method"],
        "route": path,
        "request_url": url + ("?" + query if query else ""),
    }
