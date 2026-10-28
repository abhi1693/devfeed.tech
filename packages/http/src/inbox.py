"""Explicit subscriber-only inbox operations shared by authenticated APIs."""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from devfeed_http.body_limits import RequestBodyPolicy, RequestBodyReader, RequestBodyTooLarge
from devfeed_http.operation_policies import GatewayPolicy
from devfeed_http.route_policies import RouteRuntimePolicy

INBOX_POLICY_EXTENSION = "x-devfeed-inbox"
MAX_BODY = 16_384
MAX_RESPONSE = 2_000_000
STREAM_SECONDS = 25

NotificationId = Annotated[str, Path(min_length=32, max_length=32, pattern=r"^notif_[a-z0-9]{26}$")]
BroadcastId = Annotated[str, Path(min_length=32, max_length=32, pattern=r"^bcast_[a-z0-9]{26}$")]
ItemParameter = Literal["notification_id", "broadcast_id"]
ITEM_RESOURCES: tuple[tuple[str, str, ItemParameter], ...] = (
    ("Notification", "notifications", "notification_id"),
    ("Broadcast", "broadcasts", "broadcast_id"),
)


class InboxOperation(BaseModel):
    """A registered operation owns its upstream target and transport behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, populate_by_name=True)

    name: str
    method: Literal["GET", "POST", "PUT"]
    path: str
    upstream_path: str = Field(alias="upstreamPath")
    parameter: ItemParameter | None = None
    response_mode: Literal["json", "events"] = Field(default="json", alias="responseMode")
    body_limit: int = Field(default=MAX_BODY, gt=0, alias="bodyLimit")
    body_timeout_ms: int = Field(default=2000, gt=0, alias="bodyTimeoutMs")
    upstream_timeout_ms: int = Field(default=45_000, gt=0, alias="upstreamTimeoutMs")
    response_limit: int = Field(default=MAX_RESPONSE, gt=0, alias="responseLimit")
    stream_seconds: int = Field(default=STREAM_SECONDS, gt=0, alias="streamSeconds")

    def openapi_extra(self) -> dict[str, Any]:
        return {
            INBOX_POLICY_EXTENSION: self.model_dump(
                by_alias=True, exclude={"name", "method", "path", "parameter"}
            )
        }

    def upstream_url(self, api_url: str, parameters: dict[str, str]) -> str:
        values = {key: quote(value, safe="") for key, value in parameters.items()}
        return api_url.rstrip("/") + self.upstream_path.format(**values)


INBOX_OPERATIONS = (
    InboxOperation(name="Items", method="GET", path="/items", upstream_path="/v1/inbox/items"),
    InboxOperation(name="Counts", method="GET", path="/counts", upstream_path="/v1/inbox/counts"),
    InboxOperation(
        name="PreferencesRead",
        method="GET",
        path="/preferences",
        upstream_path="/v1/inbox/preferences",
    ),
    InboxOperation(
        name="PreferencesUpdate",
        method="PUT",
        path="/preferences",
        upstream_path="/v1/inbox/preferences",
    ),
    InboxOperation(
        name="Stream",
        method="GET",
        path="/stream",
        upstream_path="/v1/inbox/stream",
        response_mode="events",
    ),
    *(
        InboxOperation(name=name, method="POST", path="/" + path, upstream_path="/v1/inbox/" + path)
        for name, path in (
            ("ReadAll", "read-all"),
            ("SeenAll", "seen-all"),
            ("ArchiveAll", "archive-all"),
            ("ArchiveRead", "archive-read"),
        )
    ),
    *(
        InboxOperation(
            name=resource + action.capitalize(),
            method="POST",
            path="/" + collection + "/{" + parameter + "}/" + action,
            upstream_path="/v1/inbox/" + collection + "/{" + parameter + "}/" + action,
            parameter=parameter,
        )
        for resource, collection, parameter in ITEM_RESOURCES
        for action in ("read", "unread", "archive", "unarchive")
    ),
)


@dataclass(frozen=True)
class InboxSession:
    """Only the verified session supplies subscriber identity and credentials."""

    api_url: str
    environment: str
    secret: str = field(repr=False)
    subscriber_id: str
    expires_at: int
    client_factory: Callable[[], httpx.AsyncClient] = field(repr=False)

    def subscriber_headers(self) -> dict[str, str]:
        signature = hmac.new(
            self.secret.encode(), self.subscriber_id.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "X-Chimely-Environment": self.environment,
            "X-Chimely-Subscriber": self.subscriber_id,
            "X-Chimely-Subscriber-Hash": signature,
        }


class InboxConnection:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.upstream: httpx.Response | None = None
        self.closed = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with anyio.CancelScope(shield=True):
            try:
                if self.upstream is not None:
                    await self.upstream.aclose()
            finally:
                await self.client.aclose()


class InboxStreamResponse(StreamingResponse):
    """Own cleanup even if the reader disconnects before the first body chunk."""

    def __init__(
        self,
        content: AsyncGenerator[bytes, None],
        *,
        connection: InboxConnection,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self.event_iterator = content
        self.connection = connection

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # Unwind the event deadline in the same task that entered it.
            await self.event_iterator.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.connection.close()


class InboxProxy:
    @staticmethod
    def http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=3, write=5, pool=3),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def error(status: int) -> Response:
        # An upstream 401 is a service failure, not an expired browser session.
        code = status if status in {400, 404, 409, 422, 429} else 503
        return Response(
            json.dumps(
                {
                    "error": {
                        "code": "notification_service_unavailable"
                        if code == 503
                        else "invalid_request",
                        "message": "Notifications are temporarily unavailable. Please retry.",
                    }
                }
            ),
            status_code=code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @staticmethod
    def request_hints(request: Request, headers: dict[str, str]) -> dict[str, str]:
        params = {}
        for key in ("limit", "cursor", "filter", "last_event_id"):
            values = request.query_params.getlist(key)
            if len(values) > 1 or values and len(values[0]) > 2048:
                raise HTTPException(422, "Invalid inbox query")
            if values:
                params[key] = values[0]
        for key in ("if-none-match", "last-event-id"):
            if value := request.headers.get(key):
                if len(value) > 2048:
                    raise HTTPException(422, "Invalid inbox header")
                headers[key] = value
        if "last_event_id" in params:
            headers["last-event-id"] = params.pop("last_event_id")
        return params

    @staticmethod
    async def read_body(operation: InboxOperation, request: Request) -> bytes:
        reader = RequestBodyReader(
            policy=RequestBodyPolicy(
                body_limit=operation.body_limit, body_timeout_ms=operation.body_timeout_ms
            )
        )
        try:
            if reader.declared_oversize(request.scope):
                raise RequestBodyTooLarge
            body = await reader.read_body(request.receive)
        except RequestBodyTooLarge:
            raise HTTPException(413, "Inbox request is too large") from None
        except TimeoutError:
            raise HTTPException(408, "Inbox request timed out") from None
        if body is None:
            raise ClientDisconnect
        if body:
            try:
                json.loads(body)
            except (ValueError, UnicodeError):
                raise HTTPException(422, "Invalid inbox request") from None
        return body

    async def events(
        self, connection: InboxConnection, operation: InboxOperation, expires_at: int
    ) -> AsyncGenerator[bytes, None]:
        assert connection.upstream is not None
        try:
            with anyio.move_on_after(
                min(operation.stream_seconds, max(0, expires_at - time.time()))
            ):
                # Flush before Chimely's first heartbeat, which may arrive after our deadline.
                yield b": connected\n\n"
                async for chunk in connection.upstream.aiter_bytes():
                    yield chunk
        except httpx.HTTPError:
            return  # EventSource reconnects; REST remains authoritative.
        finally:
            await connection.close()

    async def forward(
        self, operation: InboxOperation, request: Request, session: InboxSession, **parameters: str
    ) -> Response:
        headers = session.subscriber_headers()
        params = self.request_hints(request, headers)
        body = await self.read_body(operation, request)
        if body:
            headers["content-type"] = "application/json"
        # TLS/client construction reads the CA bundle synchronously.
        client = await anyio.to_thread.run_sync(session.client_factory)
        connection = InboxConnection(client)
        handed_off = False
        try:
            with anyio.fail_after(operation.upstream_timeout_ms / 1000):
                upstream = await client.send(
                    client.build_request(
                        operation.method,
                        operation.upstream_url(session.api_url, parameters),
                        params=params,
                        headers=headers,
                        content=body,
                    ),
                    stream=True,
                )
                connection.upstream = upstream
                result_headers = {"Cache-Control": "no-store"}
                if etag := upstream.headers.get("etag"):
                    result_headers["ETag"] = etag
                if upstream.status_code == 304:
                    return Response(status_code=304, headers=result_headers)
                if not upstream.is_success:
                    return self.error(upstream.status_code)
                if operation.response_mode == "events":
                    if not upstream.headers.get("content-type", "").startswith("text/event-stream"):
                        return self.error(503)
                    response = InboxStreamResponse(
                        self.events(connection, operation, session.expires_at),
                        connection=connection,
                        headers={**result_headers, "X-Accel-Buffering": "no"},
                    )
                    handed_off = True
                    return response
                content = bytearray()
                async for chunk in upstream.aiter_bytes():
                    if len(content) + len(chunk) > operation.response_limit:
                        return self.error(503)
                    content.extend(chunk)
                return Response(
                    bytes(content),
                    status_code=upstream.status_code,
                    media_type="application/json",
                    headers=result_headers,
                )
        except (httpx.HTTPError, TimeoutError):
            return self.error(503)
        finally:
            if not handed_off:
                await connection.close()


class InboxRouter(APIRouter):
    """Register the supported protocol once, with audience-owned session resolution."""

    def __init__(
        self,
        *,
        session_dependency: Callable[..., Any],
        operation_prefix: str,
        gateway_policy: GatewayPolicy,
    ) -> None:
        super().__init__(prefix="/chimely/v1/inbox")
        proxy = InboxProxy()
        for operation in INBOX_OPERATIONS:
            policy = gateway_policy.model_copy(
                update={
                    "body_limit": operation.body_limit,
                    "body_timeout_ms": operation.body_timeout_ms,
                    "upstream_timeout_ms": operation.upstream_timeout_ms,
                }
            )
            self.add_api_route(
                operation.path,
                self.endpoint(proxy, operation, session_dependency),
                methods=[operation.method],
                operation_id=operation_prefix + operation.name,
                include_in_schema=False,
                response_class=Response,
                response_model=None,
                openapi_extra={
                    **policy.openapi_extra(),
                    **operation.openapi_extra(),
                    **RouteRuntimePolicy(
                        admission="stream" if operation.response_mode == "events" else "request"
                    ).openapi_extra(),
                },
            )

    @staticmethod
    def endpoint(
        proxy: InboxProxy, operation: InboxOperation, session_dependency: Callable[..., Any]
    ) -> Callable[..., Any]:
        if operation.parameter == "notification_id":

            async def notification(
                request: Request,
                session: Annotated[InboxSession, Depends(session_dependency)],
                notification_id: NotificationId,
            ) -> Response:
                return await proxy.forward(
                    operation, request, session, notification_id=notification_id
                )

            return notification
        if operation.parameter == "broadcast_id":

            async def broadcast(
                request: Request,
                session: Annotated[InboxSession, Depends(session_dependency)],
                broadcast_id: BroadcastId,
            ) -> Response:
                return await proxy.forward(operation, request, session, broadcast_id=broadcast_id)

            return broadcast

        async def static(
            request: Request, session: Annotated[InboxSession, Depends(session_dependency)]
        ) -> Response:
            return await proxy.forward(operation, request, session)

        return static
