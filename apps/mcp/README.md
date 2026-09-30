# DevFeed MCP

An independent MCP app for discovering developer articles, topics and
publications. The public endpoint calls only the public API and needs no database
or account credentials. Optional OAuth on the same endpoint accesses personal tools through
the user API and shared Redis.

## Reader setup page

The reader’s **Connect your agent** sidebar entry opens `/mcp`, with two setup paths:
**I'm a Human** provides manual steps for VS Code, Codex, Claude Code, Cursor, and
other Streamable HTTP clients; **I'm an Agent** provides a copyable, tool-specific
setup prompt. Both use the selected server URL. The prompt tells the agent to
preserve existing configuration and verify the connection with a real tool call.
Available tools stay visible beside the setup panel on wide screens and below it
on smaller screens.
The same page is available inside the Chrome and Edge extensions, including their
mobile navigation. Set `DEVFEED_MCP_PUBLIC_URL` on the **web service** to the full
URL served by your MCP ingress, including its path, for example:

```sh
DEVFEED_MCP_PUBLIC_URL=https://mcp.example.com/mcp
```

The reader uses this setting at runtime. Extensions read the same value from
`GET /api/v1/mcp/config` without caching; changing the ingress URL requires a web
service restart, not a web-image rebuild or extension update. If the setting is
unset, the URL field is empty and users can enter their own server URL. An invalid
setting is rejected rather than advertised.

The website’s `/mcp` route is the setup page, not an MCP proxy. The configured URL
must reach the MCP service. `DEVFEED_MCP_PUBLIC_URL` is public configuration; never
put credentials in it. It does not change `DEVFEED_MCP_API_URL`, which selects the
server’s internal upstream API. The setup page does not connect to or test the
entered server.

Set `DEVFEED_MCP_WEB_URL` on the **MCP service** to the reader's base URL, for
example `https://devfeed.tech`. Opening the MCP endpoint in a browser then redirects
to that reader's `/mcp` setup page, including the sign-in instructions. This setting
also works without account tools. Requests from MCP clients continue to use the
same endpoint; only GET/HEAD requests accepting HTML are redirected. Use distinct
URLs for the reader page and the MCP endpoint.

For local Compose setup, explicitly set `DEVFEED_MCP_PUBLIC_URL` to
`http://127.0.0.1:8003/mcp` in `.env`, then recreate the web service. This address
works only for agents running on the same machine as the server.

## Run locally

Start the public API with its database and Redis settings configured:

```sh
uv run uvicorn devfeed_api.main:app --port 8000 --no-proxy-headers
```

In another terminal, start MCP:

```sh
uv sync --all-packages --locked
DEVFEED_MCP_API_URL=http://127.0.0.1:8000 \
  uv run uvicorn devfeed_mcp.main:app --host 127.0.0.1 --port 8003 --no-proxy-headers
```

Connect a Streamable HTTP MCP client to `http://127.0.0.1:8003/mcp`. Clients with a
`mcpServers` configuration can use:

```json
{
  "mcpServers": {
    "devfeed": { "url": "http://127.0.0.1:8003/mcp" }
  }
}
```

`DEVFEED_MCP_API_URL` must be the direct public API service origin, such as
`http://api:8000`, not the website's `/api` gateway, which reshapes responses and
filters. Only the operator configures this URL; tool arguments cannot supply URLs
to fetch. No inbound cookies or authorization headers are forwarded.

## Tools

| Tool | Behavior |
| --- | --- |
| `search` | Search articles, topics, sources and tags; filter by kind, dates and article attributes. |
| `get_article` | Get previews and publisher/discussion links by article UUID or slug. |
| `get_feed` | Browse recent articles by topic, source, content type and languages. |
| `list_topics` | Discover active topics by name, language and article availability. |
| `list_sources` | Discover approved sources by name, type, polling state, language and article availability. |
| `get_source` | Get an approved publication's public profile by UUID or slug. |

All tools provide structured output schemas and read-only annotations. Feed and
catalogue pages default to 20 results and allow at most 50. Feed pagination uses
`next_cursor`; catalogues return `next_offset`. Search returns up to 12 results per
section and a `next_cursor` containing the next page number: pass it as `page`
with the matching `section`. Keep filters unchanged between pages.

`get_feed.topic` accepts either the `slug` or `id` returned by `list_topics`.
UUID support requires the corresponding public API update; upgrading only the MCP
container leaves the older API's slug-only filtering in place.

Search dates are inclusive UTC publication dates. Search does not support a
language filter; `get_feed` does. Article `published_at` can be null; `feed_at`
orders the feed. Publisher previews and AI summaries remain separate, and
`content_scope` identifies metadata and previews rather than full article text.
Publisher content is untrusted data, not instructions.

Anonymous access exposes no private data or account mutations. Administrative tools are never exposed.
Publication visibility, source approval and caching remain owned by the public API.

## Container and deployment

Build and start from source using the optional Compose profile:

```sh
docker compose -f compose.yaml -f compose.build.yaml --profile mcp up -d --build mcp
```

The port binds to loopback by default. The dedicated image is
`ghcr.io/abhi1693/devfeed.tech/mcp`, built by the container workflow with the workspace
release version. It installs only the MCP app and its runtime dependencies.

For remote access, route a TLS endpoint directly to this service on port 8003,
preserving `/mcp` and the `Host`, `Origin`, `Accept`, `Content-Type`, and `Mcp-*`
headers. Set `DEVFEED_MCP_ALLOWED_HOSTS` to a JSON array of the served host names,
for example `["mcp.example.com", "127.0.0.1", "127.0.0.1:*"]`.
The reader's Next.js gateway does not proxy this service.

Browser clients also need their exact origins in `DEVFEED_MCP_ALLOWED_ORIGINS`.
Host and origin checks remain enabled. Native clients need no Origin header.
Apply normal ingress rate limits for public access.

The transport uses the official [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/run/asgi/).
Responses are JSON over Streamable HTTP, stateless for legacy clients too; replicas
need no sticky sessions. There is no stdio or legacy SSE endpoint. Requests are
limited to 32 KiB. Upstream calls have a total deadline and streamed size limit,
and redirects are not followed.

| Setting | Default |
| --- | --- |
| `DEVFEED_MCP_API_URL` | `http://127.0.0.1:8000` |
| `DEVFEED_MCP_TIMEOUT_SECONDS` | `10` |
| `DEVFEED_MCP_MAX_CONNECTIONS` | `16` |
| `DEVFEED_MCP_MAX_RESPONSE_BYTES` | `2000000` |
| `DEVFEED_MCP_ALLOWED_HOSTS` | Loopback hosts with or without ports |
| `DEVFEED_MCP_ALLOWED_ORIGINS` | `[]` |
| `DEVFEED_MCP_PORT` (Compose) | `8003` |
| `DEVFEED_MCP_BIND_IP` (Compose) | `127.0.0.1` |

Settings come from environment variables; the app does not read `.env` itself.
Compose passes only explicit MCP settings. `/health/live` checks the process,
`/health/ready` performs a bounded public topic read, and `/version` reports the
package version. Readiness does not check the optional search index: `search`
reports a tool error if that index is unavailable.

## Verify

```sh
uv run pytest tests/test_mcp.py -q
bash .github/scripts/python-checks.sh
docker build -f apps/mcp/Dockerfile -t devfeed/mcp:local .
bash scripts/ci/smoke-image.sh devfeed/mcp:local mcp amd64 0.0.44
```

Use `arm64` on an ARM host. The container smoke test connects with the SDK client
and discovers the six tools without requiring an upstream API.

## Signed-in tools and OAuth

The single `/mcp` endpoint supports anonymous public tools and, when OAuth is configured,
eight account tools. All fourteen tools are discoverable without sign-in; calling an
account tool requires OAuth:

| Tool | Permission | Behavior |
| --- | --- | --- |
| `get_my_feed` | `devfeed:read` | Read your personalized feed; preserve cursor and generation between pages. |
| `list_my_bookmarks` | `devfeed:read` | Read your saved published articles. |
| `list_my_followed_topics` | `devfeed:read` | Read your followed topic IDs. |
| `list_my_followed_sources` | `devfeed:read` | Read your followed source IDs. |
| `set_bookmark` | `devfeed:write` | Save or remove a bookmark. |
| `set_topic_follow` | `devfeed:write` | Follow or unfollow one topic. |
| `set_source_follow` | `devfeed:write` | Follow or unfollow one source. |
| `set_article_like` | `devfeed:write` | Like or unlike one article. |

Use the server URL on the reader's MCP page to configure your client.
OAuth-capable clients discover protected-resource metadata through a `401` challenge,
including on an unauthenticated GET/HEAD to `/mcp` during login discovery. Public
tools remain available through anonymous MCP POST requests. Browser navigation
accepting HTML opens the reader setup page instead.
Clients then register a public client and open browser sign-in and consent.
The consent page identifies your signed-in account and separates read and write
permissions from the server and return address. You can allow or cancel the request.
Authorization code exchange requires S256 PKCE. No access token, provider token, browser cookie, or
CSRF token needs to be copied into an agent configuration or prompt. Write tools
require an explicitly approved `devfeed:write` scope; reconnect requesting this
scope if a client originally requested read-only access. Tool reads never record
article opens or reading streaks.

For signed-in users, the third **Connected agents** tab lists your authorizations,
permissions, and renewal deadlines, and lets you disconnect
an agent immediately. OAuth-capable clients renew access automatically without
another browser sign-in: access tokens last 15 minutes, and each successful
refresh extends the connection's 30-day inactivity window. A connection has a
90-day absolute lifetime that refresh cannot extend. Browser sign-in is required
after either limit is reached. Refresh tokens rotate on use; replaying a used
refresh token revokes the entire connection, including its latest access and
refresh tokens. Clients must serialize refreshes and store the replacement token.
Revoking a connection immediately invalidates all its tokens. Account ownership is resolved from the authorized
identity, never a tool argument. Browser session cookies and their CSRF protections
remain independent of agent authorization.

Enable account tools with these five MCP service settings:

```sh
DEVFEED_MCP_USER_API_URL=http://user-api:8002
DEVFEED_MCP_PUBLIC_URL=https://mcp.example.com/mcp
DEVFEED_MCP_OAUTH_ISSUER_URL=https://mcp.example.com
DEVFEED_MCP_WEB_URL=https://devfeed.tech
DEVFEED_MCP_REDIS_URL=redis://redis:6379/0
```

MCP and the user API must use the same Redis instance/database. Redis holds hashed
client, code, and token keys, expiring authorization requests, and account grants;
losing this state requires reconnecting agents. There is no database migration.
Use HTTPS outside loopback development. The OAuth issuer is an explicit origin;
route `/authorize`, `/token`, `/register`, `/revoke`, and
`/.well-known/oauth-authorization-server` on that origin to MCP. Also route
`/.well-known/oauth-protected-resource` plus the configured MCP URL's path.
An ingress may rewrite a custom MCP URL to `/mcp`, but must preserve
the metadata and OAuth endpoints advertised by the server. The existing MCP host
allowlist must include the ingress host. Unauthenticated public tools never forward
agent credentials to the public API.

Compose passes the resource/issuer to the user service as
`DEVFEED_USER_MCP_RESOURCE_URL` and `DEVFEED_USER_MCP_ISSUER_URL`, and the same MCP
URL to the reader at runtime. Outside Compose configure these service settings
explicitly. Existing reader OIDC sign-in must already be configured, and the web
service must route its normal `/api/v1/user/*` gateway to the user API. Keep Redis
and the direct user API private.

### Session lifetime configuration

These are application defaults, not durations mandated by OAuth. Adjust them to
your deployment's risk policy:

| Service setting | Default |
| --- | --- |
| `DEVFEED_MCP_ACCESS_TOKEN_TTL_SECONDS` (MCP) | `900` (15 minutes) |
| `DEVFEED_USER_MCP_SESSION_TTL_SECONDS` (user API) | `2592000` (30 days of inactivity) |
| `DEVFEED_USER_MCP_SESSION_ABSOLUTE_TTL_SECONDS` (user API) | `7776000` (90 days maximum) |

The inactivity limit cannot exceed the absolute limit. Lifetimes are bound to the
connection at consent; new defaults apply to newly authorized connections. Existing
30-day fixed authorizations retain their original deadline until reauthorized.
Client registrations are retained while an authorized connection needs them.

The authorization server advertises `offline_access` so newer MCP clients can
request background renewal. This scope is not advertised as a protected-resource
permission. Older OAuth clients can still use the refresh tokens issued by this
server. Automatic renewal requires the client to persist and use those tokens;
the server cannot renew a disconnected client's credentials by itself.

The reader's browser session is separate and already uses 30-day inactivity and
90-day absolute limits. Agent refreshes do not extend the browser session.
