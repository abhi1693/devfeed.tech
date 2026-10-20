"""Gateway transport policy belongs to registered operations, independently of URL spelling."""

import copy
import hashlib
from typing import Any, Literal

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from starlette.convertors import CONVERTOR_TYPES

from devfeed_http.body_limits import RequestBodyPolicy

GATEWAY_POLICY_EXTENSION = "x-devfeed-gateway"
GATEWAY_ROUTES_EXTENSION = "x-devfeed-gateway-routes"


class GatewayPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, populate_by_name=True)

    authentication: Literal["browser", "logout-token"] = Field(
        default="browser", exclude_if=lambda value: value == "browser"
    )
    body_limit: int = Field(default=1_000_000, gt=0, alias="bodyLimit")
    body_timeout_ms: int = Field(default=2000, gt=0, alias="bodyTimeoutMs")
    upstream_timeout_ms: int = Field(default=45_000, gt=0, alias="upstreamTimeoutMs")

    def openapi_extra(self) -> dict[str, Any]:
        return {GATEWAY_POLICY_EXTENSION: self.model_dump(by_alias=True)}

    def bounded_openapi_extra(
        self, *, rejection: Literal["json", "image"] = "json"
    ) -> dict[str, Any]:
        """Apply the same byte limit and deadline to the registered backend endpoint."""
        body_policy = RequestBodyPolicy(
            body_limit=self.body_limit, body_timeout_ms=self.body_timeout_ms, rejection=rejection
        )
        return {**self.openapi_extra(), **body_policy.openapi_extra()}


class ExtensionTransportPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, populate_by_name=True)

    credentials: Literal["include", "omit"] = "include"
    cache_control: Literal["public-read", "no-store"] | None = Field(
        default=None, alias="cacheControl"
    )
    request_headers: tuple[Literal["Content-Type", "X-CSRF-Token", "If-None-Match"], ...] = Field(
        default=("Content-Type", "X-CSRF-Token", "If-None-Match"), alias="requestHeaders"
    )
    timeout_ms: int = Field(default=15_000, gt=0, alias="timeoutMs")


class ReaderGatewayPolicy(GatewayPolicy):
    cookie_policy: Literal["reader", "reader-attribution", "omit"] = Field(
        default="reader", alias="cookiePolicy"
    )
    handler: Literal["forward", "placement-query"] = "forward"
    extension: ExtensionTransportPolicy | None = None


DEFAULT_GATEWAY_POLICY = GatewayPolicy()


def include_gateway_router(
    app: FastAPI, router: APIRouter, *, default_policy: GatewayPolicy = DEFAULT_GATEWAY_POLICY
) -> None:
    """Expose this router's operations, retaining endpoint overrides and unrelated metadata."""
    for context in iter_route_contexts(router.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            extra = dict(route.openapi_extra or {})
            policy = type(default_policy).model_validate(
                extra.get(GATEWAY_POLICY_EXTENSION, default_policy.model_dump())
            )
            route.openapi_extra = {**extra, **policy.openapi_extra()}
    app.include_router(router)


def export_gateway_document(
    app: FastAPI, *, policy_type: type[GatewayPolicy] = GatewayPolicy
) -> dict[str, Any]:
    """Export the schema plus exposed routes, including operations hidden from Swagger."""
    document = copy.deepcopy(app.openapi())
    operations = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        extra = context.openapi_extra or {}
        if GATEWAY_POLICY_EXTENSION not in extra:
            continue
        policy = policy_type.model_validate(extra[GATEWAY_POLICY_EXTENSION])
        for method in sorted(context.methods or ()):
            operation = (
                document.get("paths", {}).get(context.path_format, {}).get(method.lower(), {})
            )
            resolved_policy = policy.model_dump(by_alias=True, mode="json")
            if operation:
                # FastAPI excludes nested nulls from OpenAPI. A denied extension
                # and an unset cache header must remain explicit transport policy.
                operation[GATEWAY_POLICY_EXTENSION] = copy.deepcopy(resolved_policy)
            path_fields = {
                parameter["name"]: parameter["schema"]
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "path"
            }
            fields = {field.alias: field for field in context.dependant.path_params}
            parameters = []
            for name, converter in context.param_convertors.items():
                converter_name = next(
                    (
                        kind
                        for kind, registered in CONVERTOR_TYPES.items()
                        if type(converter) is type(registered)
                    ),
                    None,
                )
                if converter_name is None:
                    raise ValueError(f"Unsupported gateway path converter for {name}")
                schema = path_fields.get(name)
                if schema is None:
                    if name not in fields:
                        raise ValueError(f"Gateway path parameter {name} has no validation schema")
                    schema = TypeAdapter(fields[name].field_info.rebuild_annotation()).json_schema()
                parameters.append({"name": name, "schema": schema, "converter": converter_name})
            identity = operation.get("operationId") or route.operation_id
            if identity is None:
                digest = hashlib.sha256(f"{method} {context.path_format}".encode()).hexdigest()[:12]
                identity = f"{context.name}_{method.lower()}_{digest}"
            operations.append(
                {
                    "operationId": identity,
                    "method": method,
                    "path": context.path_format,
                    "parameters": parameters,
                    "policy": resolved_policy,
                }
            )
    document[GATEWAY_ROUTES_EXTENSION] = operations
    return document
