"""Bounded REST/JSON connectors interpreted as data, never executable code."""

import hashlib
import json
import os
import re
from typing import Annotated, Literal
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.partner_tools import ProductInput
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.redis import create_redis
from devfeed_core.schemas import InputModel
from devfeed_core.urls import validate_public_url

Path = Annotated[
    str,
    StringConstraints(
        max_length=200,
        pattern=r"^(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)(?:\.(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+))*$|^$",
    ),
]
Parameter = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
]


class ConnectorAuth(InputModel):
    mode: Literal["none", "bearer", "api_key"] = "none"
    secret_ref: str = Field(default="", pattern=r"^$|^DEVFEED_PARTNER_SECRET_[A-Z0-9_]{1,80}$")
    header: Parameter = "X-API-Key"

    @model_validator(mode="after")
    def valid_auth(self):
        if self.mode != "none" and not self.secret_ref:
            raise ValueError("Authentication requires a partner secret reference")
        if self.header.lower() in {
            "host",
            "cookie",
            "connection",
            "content-length",
            "transfer-encoding",
            "proxy-authorization",
            "accept-encoding",
        }:
            raise ValueError("This authentication header is not allowed")
        return self


class ConnectorPagination(InputModel):
    mode: Literal["none", "cursor", "page", "offset", "next_url"] = "cursor"
    parameter: Parameter = "cursor"
    next_path: Path = "nextCursor"
    size_parameter: Parameter = "limit"
    page_size: int = Field(default=10, ge=1, le=100)
    start: int = Field(default=1, ge=0, le=1000000)
    total_path: Path | None = None
    has_more_path: Path | None = None


class ConnectorFields(InputModel):
    external_id: list[Path] = Field(default_factory=lambda: ["id"], min_length=1, max_length=5)
    name: list[Path] = Field(default_factory=lambda: ["name"], min_length=1, max_length=5)
    product_url: list[Path] = Field(default_factory=lambda: ["url"], min_length=1, max_length=5)
    listing_url: list[Path] = Field(
        default_factory=lambda: ["listing_url"], min_length=1, max_length=5
    )
    description: list[Path] = Field(
        default_factory=lambda: ["description"], min_length=1, max_length=5
    )
    pricing: list[Path] = Field(default_factory=lambda: ["pricing"], min_length=1, max_length=5)


class ConnectorFilter(InputModel):
    path: Path
    values: list[str] = Field(min_length=1, max_length=20)
    include_missing: bool = True

    @field_validator("values")
    @classmethod
    def bounded_values(cls, values):
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Filter values must be nonempty and at most 200 characters")
        return values


class ConnectorConfig(InputModel):
    version: Literal[1] = 1
    base_url: str
    list_path: str = Field(default="/products", min_length=1, max_length=500)
    detail_path: str | None = Field(default=None, max_length=500)
    items_paths: list[Path] = Field(default_factory=lambda: ["items"], min_length=1, max_length=5)
    detail_root: Path = ""
    fields: ConnectorFields = Field(default_factory=ConnectorFields)
    pagination: ConnectorPagination = Field(default_factory=ConnectorPagination)
    auth: ConnectorAuth = Field(default_factory=ConnectorAuth)
    filters: list[ConnectorFilter] = Field(default_factory=list, max_length=10)
    parameters: dict[Parameter, str] = Field(default_factory=dict, max_length=20)
    platform_hosts: list[str] = Field(default_factory=list, max_length=5)
    listing_url_template: str | None = Field(default=None, max_length=500)
    attribution: str = Field(default="", max_length=300)
    requests_per_minute: int = Field(default=60, ge=1, le=600)
    timeout_seconds: int = Field(default=15, ge=1, le=30)
    max_response_bytes: int = Field(default=2000000, ge=1024, le=5000000)
    max_pages: int = Field(default=1000, ge=1, le=1000)

    @field_validator("base_url")
    @classmethod
    def public_origin(cls, value):
        value = validate_public_url(value)
        parts = urlsplit(value)
        if parts.path != "/" or parts.query or parts.scheme != "https":
            raise ValueError("API base URL must be a public HTTPS origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def valid_endpoints(self):
        for path in [self.list_path, self.detail_path]:
            if path is None:
                continue
            if not path.startswith("/") or path.startswith("//") or "\\" in path or "#" in path:
                raise ValueError("API endpoints must be paths on the configured origin")
            if path == self.list_path and ("{" in path or "}" in path):
                raise ValueError("List endpoint cannot contain placeholders")
            if path == self.detail_path and (
                "{id}" not in path or re.search(r"[{}]", path.replace("{id}", ""))
            ):
                raise ValueError("Detail endpoint requires only the {id} placeholder")
            validate_public_url(self.base_url + path.replace("{id}", "preview"))
        if self.pagination.parameter == self.pagination.size_parameter:
            raise ValueError("Pagination and page size parameters must differ")
        if any(
            len(value) > 1000 or any(ord(c) < 32 for c in value)
            for value in self.parameters.values()
        ):
            raise ValueError("Static query values are too long or contain control characters")
        if any(not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host) for host in self.platform_hosts):
            raise ValueError("Platform hosts must be hostnames")
        if self.listing_url_template:
            if "{id}" not in self.listing_url_template or re.search(
                r"[{}]", self.listing_url_template.replace("{id}", "")
            ):
                raise ValueError("Listing template requires only the {id} placeholder")
            validate_public_url(self.listing_url_template.replace("{id}", "preview"))
        return self


# A creation preset, not a dispatch table. Persisted definitions drive imports.
NICK_CONNECTOR = {
    "base_url": "https://nicklaunches.com",
    "list_path": "/api/v1/products/",
    "detail_path": "/api/v1/products/{id}/",
    "items_paths": ["items", "results"],
    "fields": {
        "external_id": ["slug"],
        "name": ["name"],
        "product_url": ["url", "productUrl"],
        "listing_url": ["productUrl", "url"],
        "description": ["description", "tagline"],
        "pricing": ["pricing"],
    },
    "platform_hosts": ["nicklaunches.com", "www.nicklaunches.com"],
    "filters": [{"path": "categories", "values": ["Developer Tools"], "include_missing": True}],
    "attribution": "Via Nick Launches",
}


def connector_snapshot(connection):
    raw = connection.connector
    if not raw and connection.provider == "nick-launches":
        raw = NICK_CONNECTOR
    return ConnectorConfig.model_validate(raw).model_dump(mode="json")


def value_at(data, path):
    for segment in path.split(".") if path else []:
        if isinstance(data, dict):
            if segment not in data:
                raise ValueError("Configured response path was not found")
            data = data[segment]
        elif isinstance(data, list) and segment.isdecimal() and int(segment) < len(data):
            data = data[int(segment)]
        else:
            raise ValueError("Configured response path was not found")
    return data


def first_value(data, paths, *, required=False):
    for path in paths:
        try:
            value = value_at(data, path)
            if value is not None and value != "":
                return value
        except ValueError:
            pass
    if required:
        raise ValueError("Required mapped field was not found")
    return None


def same_origin(config, value):
    value = validate_public_url(urljoin(config.base_url + "/", value))
    if urlsplit(value)[:2] != urlsplit(config.base_url)[:2]:
        raise ValueError("API URLs must stay on the configured origin")
    return value


def api_json(provider, config, url):
    url = same_origin(config, url)
    headers = {}
    if config.auth.mode != "none":
        secret = os.environ.get(config.auth.secret_ref)
        if not secret or len(secret) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in secret):
            raise ValueError("Partner credential is unavailable or invalid")
        headers["Authorization" if config.auth.mode == "bearer" else config.auth.header] = (
            f"Bearer {secret}" if config.auth.mode == "bearer" else secret
        )

    # Share the budget across preview and every worker/product request for this provider.
    def before_request(target):
        same_origin(config, target)
        with create_redis(get_settings()) as redis:
            retry = consume_rate_limits(
                redis,
                [
                    RateLimitBudget(
                        key="partner-api-budget:" + hashlib.sha256(provider.encode()).hexdigest(),
                        limit=config.requests_per_minute,
                        window=60,
                    )
                ],
            )
        if retry:
            raise FeedError("Partner request budget exhausted", retryable=True, retry_after=retry)

    response = _fetch(
        url,
        None,
        None,
        accept="application/json",
        max_bytes=config.max_response_bytes,
        timeout=config.timeout_seconds,
        origin_headers=headers,
        before_request=before_request,
    )
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Partner returned invalid JSON") from exc


def normalized_filter(value):
    return str(value).casefold().replace("-", " ").strip()


def included(config, data):
    for rule in config.filters:
        try:
            value = value_at(data, rule.path)
        except ValueError:
            if rule.include_missing:
                continue
            return False
        if value is None:
            if rule.include_missing:
                continue
            return False
        choices = value if isinstance(value, list) else [value]
        if not any(
            normalized_filter(choice) in {normalized_filter(v) for v in rule.values}
            for choice in choices
        ):
            return False
    return True


def discover_page(provider, config, cursor=None, *, fetch=None):
    config = ConnectorConfig.model_validate(config)
    pagination = config.pagination
    params = {**config.parameters, pagination.size_parameter: str(pagination.page_size)}
    endpoint = config.base_url + config.list_path
    if pagination.mode == "next_url" and cursor:
        endpoint = same_origin(config, cursor)
    elif pagination.mode in {"page", "offset"}:
        start = pagination.start if pagination.mode == "page" else 0
        try:
            position = int(cursor) if cursor is not None else start
        except ValueError as exc:
            raise ValueError("Invalid pagination position") from exc
        if position < 0 or position > 1000000000:
            raise ValueError("Invalid pagination position")
        params[pagination.parameter] = str(position)
    elif pagination.mode == "cursor" and cursor:
        if not isinstance(cursor, str) or len(cursor) > 2000:
            raise ValueError("Invalid pagination cursor")
        params[pagination.parameter] = cursor
    parts = urlsplit(endpoint)
    # Server-provided next URLs own their exact query (including signed cursors).
    if pagination.mode != "next_url" or not cursor:
        endpoint = urlunsplit(
            parts._replace(query=urlencode([*parse_qsl(parts.query), *params.items()]))
        )
    body = (fetch or (lambda url: api_json(provider, config, url)))(endpoint)
    items = first_value(body, config.items_paths, required=True)
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("Partner returned an invalid product page")
    next_cursor = None
    if pagination.mode in {"cursor", "next_url"}:
        next_cursor = value_at(body, pagination.next_path)
        if (
            next_cursor is not None
            and next_cursor != ""
            and (not isinstance(next_cursor, str) or len(next_cursor) > 2000)
        ):
            raise ValueError("Partner returned an invalid pagination token")
        next_cursor = next_cursor or None
        if pagination.mode == "next_url" and next_cursor:
            next_cursor = same_origin(config, urljoin(endpoint, next_cursor))
    elif pagination.mode in {"page", "offset"}:
        more = len(items) >= pagination.page_size
        if pagination.has_more_path:
            more = value_at(body, pagination.has_more_path)
            if not isinstance(more, bool):
                raise ValueError("Pagination has-more field must be a boolean")
        elif pagination.total_path:
            total = value_at(body, pagination.total_path)
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ValueError("Pagination total must be a nonnegative integer")
            more = (
                (position - pagination.start) * pagination.page_size + len(items) < total
                if pagination.mode == "page"
                else position + len(items) < total
            )
        if more and not items:
            raise ValueError("Pagination cannot advance without products")
        if more:
            next_cursor = str(position + (1 if pagination.mode == "page" else len(items)))
    candidates = []
    for entry in items:
        data = entry if isinstance(entry, dict) else {"invalid_record": entry}
        if not included(config, data):
            continue
        identity = first_value(data, config.fields.external_id)
        identity = (
            str(identity)
            if isinstance(identity, (str, int)) and not isinstance(identity, bool)
            else ""
        )
        if not identity.strip() or len(identity) > 200 or identity in {".", ".."}:
            identity = (
                "invalid-" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            )
        candidates.append({"external_id": identity, "data": data})
    return candidates, next_cursor


def mapped_product(provider, config, candidate, *, fetch=None):
    config = ConnectorConfig.model_validate(config)
    identity = first_value(candidate.data, config.fields.external_id, required=True)
    if str(identity) != candidate.external_id or str(identity) in {".", ".."}:
        raise ValueError("Partner product identity is missing or invalid")
    data = candidate.data
    if config.detail_path:
        url = config.base_url + config.detail_path.replace("{id}", quote(str(identity), safe=""))
        data = value_at(
            (fetch or (lambda target: api_json(provider, config, target)))(url), config.detail_root
        )
        if (
            str(first_value(data, config.fields.external_id, required=True))
            != candidate.external_id
        ):
            raise ValueError("Partner returned a different product identity")
    hosts = {host.lower() for host in config.platform_hosts}

    def mapped_url(paths, listing):
        for path in paths:
            value = first_value(data, [path])
            if not isinstance(value, str):
                continue
            value = validate_public_url(value)
            if not hosts or (urlsplit(value).hostname in hosts) == listing:
                return value
        if listing and config.listing_url_template:
            return config.listing_url_template.replace("{id}", quote(str(identity), safe=""))
        raise ValueError("Product website or platform listing is missing")

    pricing = first_value(data, config.fields.pricing)
    return ProductInput(
        provider=provider,
        external_id=candidate.external_id,
        name=first_value(data, config.fields.name, required=True),
        product_url=mapped_url(config.fields.product_url, False),
        listing_url=mapped_url(config.fields.listing_url, True),
        description=first_value(data, config.fields.description, required=True),
        pricing={
            "free": "free",
            "freemium": "freemium",
            "paid": "paid",
            "subscription": "paid",
            "one time": "paid",
            "one_time": "paid",
        }.get(str(pricing or "unknown").lower(), "unknown"),
        attribution=config.attribution,
    )
