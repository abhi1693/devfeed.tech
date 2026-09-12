# Public content for AI readers

The reader app integrates [Dodo Payments' Dualmark](https://github.com/dodopayments/dualmark)
using `@dualmark/nextjs` and `@dualmark/core`. Public pages have readable Markdown
representations with publisher attribution and links to related content.

## Discovery

- `/llms.txt`: concise discovery index, directories, content types and sitemap link.
- `/llms-full.txt`: expanded reading guide plus the first page of the public feed,
  topics, sources and tags. It explicitly identifies this bounded selection and
  provides continuation links; it is not an unbounded full-archive export.
- `/sitemap.xml`: complete, paginated public URL inventory. See [sitemaps](sitemaps.md).

Both discovery files are linked from the HTML document head. URLs use
`DEVFEED_USER_BASE_URL` (default `https://devfeed.tech`), never the incoming Host.

## Markdown access

Request `Accept: text/markdown` on a supported public page, or append `.md` to its
path. `/` maps to `/index.md`. Supported pages include article previews, topic,
source and tag feeds, topic/source directories, content-type feeds and search.
`/tags.md` is an additional Markdown directory; no HTML `/tags` index is advertised.

```sh
curl -H 'Accept: text/markdown' https://devfeed.tech/articles/example-slug
curl https://devfeed.tech/topics.md
curl 'https://devfeed.tech/search.md?q=kubernetes'
```

Dualmark negotiates the format. Known AI user agents can receive Markdown when
Accept permits it, but an explicit HTML preference is respected. Normal browser
responses advertise the Markdown alternative through a Link header, retaining
query parameters. Markdown responses vary on both Accept and User-Agent. HTML
responses remain private and uncacheable: Next.js owns their final Vary header,
and replaces the proxy's value with its React navigation fields. React
Server Component requests, navigation prefetches and server actions bypass
negotiation so client navigation continues to work.

Markdown responses include `text/markdown`, `X-Markdown-Tokens` (an estimate),
`X-AEO-Version`, `X-Robots-Tag: noindex`, a canonical link and an ETag. The internal
`/md/...` handler applies the same public allowlist even when accessed directly.

## Content and boundaries

Article representations match the public preview: metadata, an original-publisher
link, topics, tags, sources, AI overview and source excerpt where present. They do
not retrieve or republish original full articles, call `/open`, or record reader
engagement. The public API remains authoritative for publication visibility.

No request cookie or authorization header is forwarded. The AI routes cannot
access settings, personalized feeds, notifications, suggestions, admin data or
unpublished articles. This also applies when a signed-in reader requests Markdown.

Feeds are cursor-paginated at 24 articles. Directories return 60 entries per request
and expose offset continuation links. Search uses the existing indexed search API
and keeps articles first, with independent pagination per section. Article detail
requests fetch one record by slug; they never load a collection to find an entry.
This is why rendering uses Dualmark's response primitives rather than the adapter's
`getEntries()` collection scan.

## Caching and failures

`llms.txt` is database-free and publicly cacheable for one hour. Dynamic Markdown
and `llms-full.txt` use the existing shared, publication-invalidated public API
cache. They require HTTP revalidation (`public, max-age=0, must-revalidate`) so an
additional frontend cache cannot outlive a moderation invalidation. Conditional
requests return 304 when content has not changed; the upstream lookup still runs
through the public cache. No frontend request forwards cache-bypass headers.

The full document makes exactly four bounded API reads in parallel, regardless
of archive size. Ordinary HTML page loads do not fetch either discovery file.
The existing response-cache TTL, locking and outage behavior apply; unlike the
sitemap inventory, this layer does not create an independent Redis snapshot.
If public response caching is disabled or unavailable, its existing DB fallback
behavior still applies. Keep `DEVFEED_CACHE_ENABLED=true` in deployment.

Missing public content returns 404. Invalid parameters return 400/422. Dependency
failures return 503 with Retry-After, rather than an empty successful document.
Errors are not cached, and backend diagnostics are not included in their bodies.

No migrations, new workers or external AI service are required. Regression tests
cover negotiation, routing isolation, cursor and offset links, attribution,
publication errors, cache headers and bounded anonymous reads.
