# DevFeed MCP

An independent, read-only MCP app for discovering developer articles, topics and
publications. It calls the public API over HTTP and needs no database, Redis,
account credentials or access to the admin/user APIs.

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

The app exposes no engagement, personal-data, submission or administrative tools.
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
