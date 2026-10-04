"""Remove authentication material without hiding ordinary operational context."""

import re
from urllib.parse import quote, unquote

REDACTED = "[REDACTED]"
# These names are credentials even outside a URL. Avoid ambiguous fields such as
# status.code and application state; code/state are sensitive query parameters.
_CREDENTIAL_FIELDS = frozenset(
    {
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "token",
        "clientsecret",
        "codeverifier",
        "assertion",
        "clientassertion",
        "password",
        "passwd",
        "secret",
        "apikey",
        "authorization",
        "cookie",
        "setcookie",
        "samlresponse",
        "oauthtoken",
        "oauthverifier",
        "devicecode",
        "usercode",
    }
)
_QUERY_FIELDS = _CREDENTIAL_FIELDS | {"code", "state", "sessionstate", "relaystate"}
_PARAMETER = re.compile(r"(^|[?&#;\s])([\w%+.-]+)=([^&#;\s\"'<>]*)")
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/?#\s@]+@", re.IGNORECASE)
_AUTHORIZATION = re.compile(r"(\b(?:bearer|basic)\s+)[a-z0-9._~+/-]+=*", re.IGNORECASE)


def _name(value: str) -> str:
    return re.sub(r"[_.-]", "", unquote(value).casefold())


def _credential_field(value: str) -> bool:
    return _name(value.rsplit(".", 1)[-1]) in _CREDENTIAL_FIELDS


def _encoded_value(value: str, depth: int) -> str:
    decoded = unquote(value)
    if decoded == value:
        return value
    if depth >= 5:
        return REDACTED
    sanitized = _encoded_value(decoded, depth + 1)
    if "?" in sanitized or "#" in sanitized or "://" in sanitized:
        sanitized = redact_auth_text(sanitized, depth + 1)
    return quote(sanitized, safe="") if sanitized != decoded else value


def redact_auth_text(value: str, depth: int = 0) -> str:
    """Keep URL spelling/ordering intact except credentials, including nested URLs."""
    value = _AUTHORIZATION.sub(lambda match: match[1] + REDACTED, value)
    if "=" not in value and "@" not in value:
        return value
    value = _USERINFO.sub(lambda match: match[1] + REDACTED + "@", value)

    def parameter(match: re.Match[str]) -> str:
        prefix, key, original = match.groups()
        if _name(key) in _QUERY_FIELDS:
            return prefix + key + "=" + REDACTED
        if "%" in original:
            return prefix + key + "=" + _encoded_value(original, depth)
        return match[0]

    return _PARAMETER.sub(parameter, value)


def redact_authentication(value, depth: int = 0):
    """Protect structured fields and URL-bearing messages at the output boundary."""
    if isinstance(value, str):
        return redact_auth_text(value)
    if isinstance(value, (dict, list)) and depth >= 20:
        return "[truncated]"
    if isinstance(value, dict):
        output = {
            key: REDACTED if _credential_field(str(key)) else redact_authentication(item, depth + 1)
            for key, item in value.items()
        }
        if (
            isinstance(value.get("key"), str)
            and "value" in value
            and _credential_field(value["key"])
        ):
            output["value"] = (
                {"stringValue": REDACTED} if isinstance(value["value"], dict) else REDACTED
            )
        return output
    if isinstance(value, list):
        return [redact_authentication(item, depth + 1) for item in value]
    return value
