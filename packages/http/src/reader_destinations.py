"""Decode sign-in destinations with the reader's shared navigation contract."""

import re
from datetime import date
from importlib.resources import files
from typing import Literal, TypedDict
from urllib.parse import parse_qsl, quote, quote_plus, unquote

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _ValueRule(BaseModel):
    pattern: str | None = None
    min_length: int = Field(default=0, alias="minLength")
    max_length: int | None = Field(default=None, alias="maxLength")
    no_separators: bool = Field(default=False, alias="noSeparators")
    values: dict[str, str] | list[str] | None = None
    format: Literal["date", "integer"] | None = None
    min: int | None = None
    max: int | None = None

    def valid(self, value: str) -> bool:
        if not _decoded_value(value, path_parameter=self.no_separators):
            return False
        length = _length(value)
        if (
            length > _CONTRACT.max_length
            or length < self.min_length
            or (self.max_length is not None and length > self.max_length)
        ):
            return False
        if self.pattern is not None and re.fullmatch(self.pattern, value) is None:
            return False
        if self.values is not None and value not in self.values:
            return False
        if self.format == "date":
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                return False
            try:
                date.fromisoformat(value)
            except ValueError:
                return False
        if self.format == "integer":
            if re.fullmatch(r"[0-9]+", value) is None:
                return False
            number = int(value)
            if (self.min is not None and number < self.min) or (
                self.max is not None and number > self.max
            ):
                return False
        return True

    def decode_parameter(self, value: str) -> str | None:
        if isinstance(self.values, dict):
            return next((semantic for semantic, wire in self.values.items() if wire == value), None)
        return value if self.valid(value) else None

    def encode_parameter(self, value: str) -> str:
        return self.values[value] if isinstance(self.values, dict) else value


class _Parameter(BaseModel):
    name: str
    type: str


class _Alias(BaseModel):
    segments: list[str | _Parameter]
    parameters: dict[str, str] = Field(default_factory=dict)


class _Route(BaseModel):
    id: str
    segments: list[str | _Parameter]
    query: str
    login: bool
    aliases: list[_Alias] = Field(default_factory=list)


class _QueryProfile(BaseModel):
    fields: dict[str, str]
    required: list[str] = Field(default_factory=list)


class _Contract(BaseModel):
    version: Literal[1]
    max_length: int = Field(alias="maxLength")
    parameters: dict[str, _ValueRule]
    query_fields: dict[str, _ValueRule] = Field(alias="queryFields")
    query_profiles: dict[str, _QueryProfile] = Field(alias="queryProfiles")
    routes: list[_Route]


_CONTRACT = _Contract.model_validate_json(
    files("devfeed_http").joinpath("reader-destinations.json").read_text(encoding="utf-8")
)
_ROUTES = {route.id: route for route in _CONTRACT.routes}
_RAW_FORBIDDEN = re.compile(r"[\x00-\x20\x7f\\]")
_DECODED_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


def _length(value: str) -> int:
    """Use the same UTF-16 bounds as the JavaScript contract consumer."""
    return len(value.encode("utf-16-le")) // 2


def _decoded_value(value: str, *, path_parameter: bool = False) -> bool:
    if _DECODED_FORBIDDEN.search(value) or (
        path_parameter and ("/" in value or "\\" in value or value in {".", ".."})
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


class ReaderDestinationRecord(TypedDict):
    routeId: str
    parameters: dict[str, str]
    query: dict[str, str]


class ReaderDestination(BaseModel):
    """Resolved route identity, semantic parameters and approved query values."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    route_id: str = Field(alias="routeId")
    parameters: dict[str, str]
    query: dict[str, str]

    def as_record(self) -> ReaderDestinationRecord:
        return {
            "routeId": self.route_id,
            "parameters": dict(self.parameters),
            "query": dict(self.query),
        }

    @classmethod
    def from_record(cls, value: object) -> "ReaderDestination | None":
        try:
            destination = cls.model_validate(value)
        except ValidationError:
            return None
        route = _ROUTES.get(destination.route_id)
        if route is None or not route.login:
            return None
        parameters = {
            segment.name: _CONTRACT.parameters[segment.type]
            for segment in route.segments
            if isinstance(segment, _Parameter)
        }
        if set(destination.parameters) != set(parameters) or not all(
            _decoded_value(value, path_parameter=True) and parameters[name].valid(value)
            for name, value in destination.parameters.items()
        ):
            return None
        profile = _CONTRACT.query_profiles[route.query]
        if (
            not set(destination.query) <= profile.fields.keys()
            or not set(profile.required) <= destination.query.keys()
            or not all(
                _CONTRACT.query_fields[profile.fields[name]].valid(value)
                for name, value in destination.query.items()
            )
        ):
            return None
        return destination


def _match_parameters(
    segments: list[tuple[str, str]], definition: list[str | _Parameter]
) -> dict[str, str] | None:
    if len(segments) != len(definition):
        return None
    parameters: dict[str, str] = {}
    for (wire, value), segment in zip(segments, definition, strict=True):
        if isinstance(segment, str):
            if wire != segment:
                return None
        else:
            semantic = _CONTRACT.parameters[segment.type].decode_parameter(value)
            if semantic is None:
                return None
            parameters[segment.name] = semantic
    return parameters


def parse_reader_destination(value: str) -> ReaderDestination | None:
    """Resolve the existing URL wire value once, before beginning an OIDC flow."""
    if (
        not isinstance(value, str)
        or not _decoded_value(value)
        or _length(value) > _CONTRACT.max_length
        or not value.startswith("/")
        or value.startswith("//")
        or "#" in value
        or _RAW_FORBIDDEN.search(value)
        or _MALFORMED_ESCAPE.search(value)
    ):
        return None
    path, _, raw_query = value.partition("?")
    path = path[:-1] if len(path) > 1 and path.endswith("/") else path
    try:
        segments: list[tuple[str, str]] = (
            []
            if path == "/"
            else [(part, unquote(part, errors="strict")) for part in path[1:].split("/")]
        )
        if any(
            not segment or not _decoded_value(segment, path_parameter=True)
            for _, segment in segments
        ):
            return None
        query: dict[str, str] = {}
        if raw_query:
            pairs = parse_qsl(
                raw_query,
                keep_blank_values=True,
                strict_parsing=True,
                errors="strict",
                max_num_fields=max(
                    len(profile.fields) for profile in _CONTRACT.query_profiles.values()
                ),
            )
            for name, field in pairs:
                if name in query:
                    return None
                query[name] = field
    except (UnicodeError, ValueError):
        return None
    for route in _CONTRACT.routes:
        parameters = _match_parameters(segments, route.segments)
        if parameters is None:
            for alias in route.aliases:
                parameters = _match_parameters(segments, alias.segments)
                if parameters is not None:
                    parameters.update(alias.parameters)
                    break
        if parameters is not None:
            destination = ReaderDestination.from_record(
                {"routeId": route.id, "parameters": parameters, "query": query}
            )
            return destination if serialize_reader_destination(destination) is not None else None
    return None


def serialize_reader_destination(value: object) -> str | None:
    """Revalidate stored route state and emit a relative, single-encoded location."""
    record = value.as_record() if isinstance(value, ReaderDestination) else value
    destination = ReaderDestination.from_record(record)
    if destination is None:
        return None
    route = _ROUTES[destination.route_id]
    path = "/" + "/".join(
        quote(
            segment
            if isinstance(segment, str)
            else _CONTRACT.parameters[segment.type].encode_parameter(
                destination.parameters[segment.name]
            ),
            safe="~!*'()",
        )
        for segment in route.segments
    )
    if destination.query:
        # URLSearchParams encodes '~' and preserves '*'; urllib's defaults differ.
        query = "&".join(
            f"{quote_plus(name, safe='*').replace('~', '%7E')}="
            f"{quote_plus(field, safe='*').replace('~', '%7E')}"
            for name, field in destination.query.items()
        )
        path += "?" + query
    return path if _length(path) <= _CONTRACT.max_length else None
