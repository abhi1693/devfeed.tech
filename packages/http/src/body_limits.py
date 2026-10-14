"""Endpoint-owned body policies, enforced before parsing or dependencies."""

from typing import Any, Literal

import anyio
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

REQUEST_BODY_POLICY_EXTENSION = "x-devfeed-request-body"


class RequestBodyTooLarge(ValueError):
    pass


class RequestBodyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, populate_by_name=True)

    body_limit: int = Field(gt=0, alias="bodyLimit")
    body_timeout_ms: int = Field(default=2000, gt=0, alias="bodyTimeoutMs")
    rejection: Literal["json", "image"] = "json"

    def openapi_extra(self) -> dict[str, Any]:
        return {REQUEST_BODY_POLICY_EXTENSION: self.model_dump(by_alias=True)}

    def response(self, status: Literal[408, 413]) -> JSONResponse:
        if self.rejection == "image":
            detail = "Choose an image under 5 MB" if status == 413 else "Image upload timed out"
            headers = None
        else:
            detail = "Request too large" if status == 413 else "Incomplete request"
            headers = {"Cache-Control": "no-store"}
        return JSONResponse({"detail": detail}, status_code=status, headers=headers)


class BoundedRequestBody:
    """Read and replay one body using the policy of an already selected endpoint."""

    def __init__(self, app: ASGIApp, *, policy: RequestBodyPolicy):
        self.app, self.policy = app, policy

    def declared_oversize(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.policy.body_limit:
                        return True
                except ValueError:
                    pass  # The actual byte count remains authoritative.
        return False

    async def read_body(self, receive: Receive) -> bytes | None:
        body = bytearray()
        with anyio.fail_after(self.policy.body_timeout_ms / 1000):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return None
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > self.policy.body_limit:
                    raise RequestBodyTooLarge
                body.extend(chunk)
                if not message.get("more_body", False):
                    return bytes(body)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            if self.declared_oversize(scope):
                raise RequestBodyTooLarge
            body = await self.read_body(receive)
        except (RequestBodyTooLarge, TimeoutError) as exc:
            status: Literal[408, 413] = 413 if isinstance(exc, RequestBodyTooLarge) else 408
            await self.policy.response(status)(scope, receive, send)
            return
        if body is None:
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


class BodyLimitedRoute(APIRoute):
    """Use native routing, then bound the body before FastAPI parses the request."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        extra = self.openapi_extra or {}
        self.body_policy = (
            RequestBodyPolicy.model_validate(extra[REQUEST_BODY_POLICY_EXTENSION])
            if REQUEST_BODY_POLICY_EXTENSION in extra
            else None
        )
        self._body_handler: ASGIApp = (
            BoundedRequestBody(super().handle, policy=self.body_policy)
            if self.body_policy is not None
            else super().handle
        )

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.methods and scope["method"] not in self.methods:
            await super().handle(scope, receive, send)
            return
        # Included routers build a fresh effective handler. Guarding handle()
        # keeps the policy ahead of that handler, including nested includes.
        await self._body_handler(scope, receive, send)
