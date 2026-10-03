"""Account tools; identity comes exclusively from the OAuth request context."""

import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

import anyio
import httpx
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from devfeed_mcp.api import PublicAPI
from devfeed_mcp.schemas import Article, FeedPage
from devfeed_mcp.server import READ_ONLY, Limit

WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


class PersonalFeed(FeedPage):
    generation: UUID | None = None
    refreshing: bool = False
    feed_kind: Literal["personalized", "following", "latest"] = "personalized"


class DailyMustReads(BaseModel):
    date: date
    timezone: str
    items: list[Article] = Field(max_length=5)
    reasons: dict[str, str]
    read_ids: list[UUID]
    preparing: bool = False


class Topics(BaseModel):
    topic_ids: list[UUID]


class Sources(BaseModel):
    source_ids: list[UUID]


class Bookmark(BaseModel):
    article_id: UUID
    bookmarked: bool


class Follow(BaseModel):
    followed: bool


class Like(BaseModel):
    article_id: UUID
    liked: bool
    likes: int
    bookmarked: bool = False


class UserAPI(PublicAPI):
    async def request(self, method: str, path: str, *, scope: str, payload=None, **params):
        token = get_access_token()
        if not token or scope not in token.scopes:
            raise ToolError(
                "This tool requires DevFeed " + scope + " permission. Reconnect and approve it."
            )
        try:
            with anyio.fail_after(self.settings.timeout_seconds):
                async with self.client.stream(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {token.token}"},
                    params={k: v for k, v in params.items() if v is not None},
                    json=payload,
                ) as response:
                    if response.status_code == 401:
                        raise ToolError(
                            "Your agent authorization expired or was revoked. Reconnect to DevFeed."
                        )
                    if response.status_code == 403:
                        raise ToolError("Your agent does not have permission for this action.")
                    if response.status_code in {404, 409, 422}:
                        raise ToolError(
                            "Record unavailable or parameters outdated. "
                            "Check IDs and restart pagination if needed."
                        )
                    if not response.is_success:
                        raise ToolError("DevFeed is temporarily unavailable.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_response_bytes:
                            raise ToolError("Response too large. Use a smaller page.")
                    return json.loads(body)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
            raise ToolError("DevFeed is temporarily unavailable.") from None


def register_personal_tools(server: MCPServer, api: UserAPI):
    @server.tool(annotations=READ_ONLY)
    async def get_my_feed(
        limit: Limit = 20,
        cursor: Annotated[str, Field(max_length=300)] | None = None,
        generation: UUID | None = None,
        sort: Literal["recommended", "newest", "most_liked"] = "recommended",
    ) -> PersonalFeed:
        """Get your personalized feed. Preserve generation and cursor for subsequent pages.

        Reading this tool does not mark articles as read or change reading streaks.
        """
        return PersonalFeed.model_validate(
            await api.request(
                "GET",
                "v1/user/feed",
                scope="devfeed:read",
                limit=limit,
                cursor=cursor,
                generation=generation,
                sort=sort,
            )
        )

    @server.tool(annotations=READ_ONLY)
    async def get_my_must_reads(
        timezone: Annotated[str, Field(min_length=1, max_length=100)] = "UTC",
    ) -> DailyMustReads:
        """Get today's stable top-five personalized Must Reads and recommendation reasons.

        Supply your IANA timezone (for example Asia/Kolkata) to match the reader's
        local calendar day. Defaults to UTC. Fewer than five picks may be available;
        preparing indicates recommendations are not ready yet. Returns article metadata
        and previews, not full publisher text. Reading this tool does not mark articles
        as read, change reading streaks, or consume the reader's daily popup.
        """
        return DailyMustReads.model_validate(
            await api.request("GET", "v1/user/must-reads", scope="devfeed:read", timezone=timezone)
        )

    @server.tool(annotations=READ_ONLY)
    async def list_my_bookmarks(
        limit: Limit = 20, cursor: Annotated[str, Field(max_length=300)] | None = None
    ) -> FeedPage:
        """Get your saved public articles. Pass next_cursor unchanged for the next page."""
        return FeedPage.model_validate(
            await api.request(
                "GET", "v1/user/bookmarks", scope="devfeed:read", limit=limit, cursor=cursor
            )
        )

    @server.tool(annotations=WRITE)
    async def set_bookmark(article_id: UUID, bookmarked: bool) -> Bookmark:
        """Explicitly save or remove an article bookmark for your account."""
        return Bookmark.model_validate(
            await api.request(
                "PUT",
                f"v1/user/articles/{article_id}/bookmark",
                scope="devfeed:write",
                payload={"bookmarked": bookmarked},
            )
        )

    @server.tool(annotations=READ_ONLY)
    async def list_my_followed_topics() -> Topics:
        """Get IDs of your followed active topics. Use list_topics for their public details."""
        return Topics.model_validate(
            await api.request("GET", "v1/user/preferences", scope="devfeed:read")
        )

    @server.tool(annotations=WRITE)
    async def set_topic_follow(topic_id: UUID, followed: bool) -> Follow:
        """Explicitly follow or unfollow one topic without replacing your other follows."""
        return Follow.model_validate(
            await api.request(
                "PUT",
                f"v1/user/preferences/topics/{topic_id}",
                scope="devfeed:write",
                payload={"followed": followed},
            )
        )

    @server.tool(annotations=READ_ONLY)
    async def list_my_followed_sources() -> Sources:
        """Get IDs of your followed sources. Use list_sources for their public details."""
        return Sources.model_validate(
            await api.request("GET", "v1/user/preferences/sources", scope="devfeed:read")
        )

    @server.tool(annotations=WRITE)
    async def set_source_follow(source_id: UUID, followed: bool) -> Follow:
        """Explicitly follow or unfollow one source without replacing other follows."""
        return Follow.model_validate(
            await api.request(
                "PUT",
                f"v1/user/preferences/sources/{source_id}",
                scope="devfeed:write",
                payload={"followed": followed},
            )
        )

    @server.tool(annotations=WRITE)
    async def set_article_like(article_id: UUID, liked: bool) -> Like:
        """Explicitly like or unlike an article for your account; never records a read."""
        return Like.model_validate(
            await api.request(
                "PUT",
                f"v1/user/articles/{article_id}/like",
                scope="devfeed:write",
                payload={"liked": liked},
            )
        )


class AccountToolAuth:
    """Challenge OAuth discovery and account calls on the shared transport."""

    def __init__(self, required_auth):
        self.required_auth = required_auth
        self.public_transport = required_auth.app

    async def __call__(self, scope, receive, send):
        import json

        from starlette.responses import JSONResponse

        authenticated = any(key.lower() == b"authorization" for key, _ in scope["headers"])
        replay = receive
        account_call = False
        if scope.get("method") == "POST":
            messages = []
            body = bytearray()
            while True:
                message = await receive()
                messages.append(message)
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 32768:
                    await JSONResponse({"detail": "Request too large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break
            try:
                request = json.loads(body)
                params = request.get("params", {}) if isinstance(request, dict) else {}
                account_call = (
                    isinstance(request, dict)
                    and request.get("method") == "tools/call"
                    and isinstance(params, dict)
                    and params.get("name") in ACCOUNT_TOOLS
                )
            except (ValueError, UnicodeError):
                pass

            async def replay():
                return messages.pop(0) if messages else await receive()

        discovery = scope.get("method") in {"GET", "HEAD"}
        app = (
            self.required_auth
            if authenticated or account_call or discovery
            else self.public_transport
        )
        await app(scope, replay, send)


ACCOUNT_TOOLS = frozenset(
    {
        "get_my_feed",
        "get_my_must_reads",
        "list_my_bookmarks",
        "set_bookmark",
        "list_my_followed_topics",
        "set_topic_follow",
        "list_my_followed_sources",
        "set_source_follow",
        "set_article_like",
    }
)
