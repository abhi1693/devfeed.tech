# Public sitemaps

`/sitemap.xml` is the sitemap index advertised by `/robots.txt`. It links to separate
article, topic, tag and source files, split at 1,000 URLs per file:

```text
/sitemap-articles-1.xml?v=<snapshot>
/sitemap-topics-1.xml?v=<snapshot>
/sitemap-tags-1.xml?v=<snapshot>
/sitemap-sources-1.xml?v=<snapshot>
```

The files contain canonical reader URLs, not API resources or original publisher
URLs. Only approved, published articles enter the inventory. Topics must be active;
sources must be approved and enabled; topics, tags and sources must have publicly
visible articles. Tag URLs use `/tags/<slug>` with a public feed and indexable
metadata. Search, account, administrative, unpublished and empty catalogue pages
are excluded. Filtered tag feeds remain `noindex` like other refined feeds.

## Cache and freshness

The public API keeps shared sitemap snapshots in the existing Redis/Valkey service,
including Sentinel deployments. Every API replica uses the same database-specific
namespace. The snapshots are independent of frequently invalidated reader caches
and remain enabled even if ordinary response caching is disabled.

`DEVFEED_SITEMAP_REFRESH_SECONDS` defaults to **900 seconds**. A crawler request may
refresh an expired snapshot, but a shared lease allows only one builder. It streams
small projections from PostgreSQL in a read-only, repeatable-read transaction and
publishes the manifest only after every file is stored. Reading a current index or
any stored file requires **zero database queries**. Ordinary reader page loads do
not fetch sitemap data.

Snapshot IDs keep a crawler's index and child files consistent during refresh.
Earlier files remain available for roughly four refresh intervals plus five minutes
(default: 65 minutes, with an extra minute for child files). Unreferenced/failed-build
files expire automatically. Publication changes appear on the next snapshot refresh;
an already advertised URL can remain in a cached sitemap temporarily, while its
page still enforces current public visibility.

If a refresh fails, the previous cached snapshot remains available until its
retention expires, with a one-minute retry cooldown. Cold concurrent requests
receive `503` and `Retry-After: 5` while the first snapshot is prepared. If Redis is
unavailable, sitemap requests fail with a retryable `503`; they do not bypass the
cache and scan the database. A missing or expired snapshot file returns `404`, so
crawlers can retrieve the current index. Error responses are never publicly cached.

XML responses include ETags for conditional `304` responses. HTTP/shared-cache
lifetimes are 60 seconds for the index and 300 seconds for individual files.
Crawler request headers cannot force a PostgreSQL regeneration.

## Configuration and verification

Set `DEVFEED_USER_BASE_URL` to the reader's canonical origin (production:
`https://devfeed.tech`). If omitted outside Compose, that production origin is the
default. Request `Host` headers never determine sitemap URLs. Compose already
provides the configured reader origin; its local default is `http://localhost:3000`.
The optional refresh setting is passed to the public API:

```dotenv
DEVFEED_SITEMAP_REFRESH_SECONDS=900
```

No new service, migration, search engine or background worker is required.
Internal inventory endpoints are `/v1/sitemaps` and `/v1/sitemaps/{kind}/{page}`.
The web app serves XML through root-level filenames, keeping sitemap scope valid
for every listed reader path.

Tests verify XML encoding, origin selection, ETags, partition boundaries, public
visibility, stable snapshot versions, concurrent cache hits without SQL, failed
refresh cooldowns and cache-outage behavior. No request-time timestamps are
presented as article modification dates: the model does not track a reliable
public-content modification time for every entity, so optional `lastmod` values
are omitted.

The format and limits follow the [Sitemaps protocol](https://www.sitemaps.org/protocol.html).

## Standards and conformance

Sitemaps use the **Sitemaps 0.9 protocol**, not a sitemap-specific IETF RFC. The
XML vocabulary and its official `sitemap.xsd` and `siteindex.xsd` schemas come from
[sitemaps.org](https://www.sitemaps.org/protocol.html). URL serialization follows
[RFC 3986](https://www.rfc-editor.org/rfc/rfc3986); Unicode path text is first
percent-encoded as a URI, then XML entities are escaped. HTTP conditional reads
follow [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2).
The namespace remains `http://www.sitemaps.org/schemas/sitemap/0.9` even when the
site and sitemap locations use HTTPS.

Both published schemas require at least one child entry. Empty collections are
omitted rather than advertised as empty `urlset` documents. The index always
includes `/sitemap-pages.xml`, which lists the public homepage and needs no DB
read. This keeps an empty archive's index valid. Empty legacy child files return
404. On upgrade, the snapshot builder refreshes manifests from the old format,
while previously generated, nonempty versioned files remain readable until expiry.

The index reserves one entry for the homepage sitemap, allowing 49,999 inventory
parts plus that entry. Validation enforces the 50,000-entry maximum, the full
percent-encoded URL length (less than 2,048 characters), and the uncompressed
52,428,800-byte XML limit. Our 1,000-URL inventory parts are intentionally below
the protocol maximum. XML schema validation alone does not enforce the file-size
or 50,000-entry limits, so those are checked separately.

Direct requests to the internal `/sitemaps/{kind}/{page}` paths redirect to their
root-level public filenames, preserving the version parameter and sitemap scope.
The optional `lastmod`, `changefreq`, `priority`, gzip and schema-location hints
are not necessary for valid sitemap XML. We omit page modification dates because
no reliable public-content modification timestamp exists for every entity.
ETags use weak comparison, including across content compression, and matching
GET/HEAD requests return bodyless 304 responses with the cache metadata retained.

`apps/web/tests/sitemap-protocol.test.ts` validates actual renderer output against
checked-in, unmodified official schemas using a test-only libxml2 WebAssembly
validator. Tests run offline and cover empty archives, Unicode and XML escaping,
invalid URLs, entry/byte limits and conditional requests. The schemas and validator
are not part of the production request path.
