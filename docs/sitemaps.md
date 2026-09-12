# Public sitemaps

`/robots.txt` advertises `/sitemap.xml`. The index uses stable, root-level URLs:

```text
/sitemap-pages.xml
/sitemap-articles-1.xml
/sitemap-topics-1.xml
/sitemap-tags-1.xml
/sitemap-sources-1.xml
```

Article, topic, tag and source files contain up to 1,000 URLs each. Empty
collections are omitted. The pages sitemap includes the homepage, six content-type
feeds, and the topic and source directories. There is no HTML tag directory.
All locations use the configured reader origin and canonical routes; they are not
API resources or original publisher URLs. Only approved, published articles enter
the inventory. Topics must be active; sources must be approved and enabled;
topics, tags and sources must have publicly visible articles. Private pages,
search results, arbitrary filters and unpublished content are excluded.

## Metadata

Every page entry has `loc`, `lastmod`, `changefreq` and `priority`:

| Page | Last modification | Change frequency | Priority |
| --- | --- | --- | --- |
| Homepage | Latest public feed publication | Hourly | 1.0 |
| Content-type feed | Latest publication of that type | Hourly | 0.7 |
| Topic/source directory | Latest publication in eligible collections | Daily | 0.7 |
| Article | **Published to feed at** | Monthly | 0.8 |
| Topic, tag or source feed | Latest linked public feed publication | Daily | 0.6 |

As requested, article dates use `published_to_feed_at`, not the original publisher's
publication date or sitemap generation time. Legacy articles without a feed
publication timestamp use their persisted discovery time. Collection dates include
only publicly visible articles and eligible topic memberships. Empty browsing pages
use the web artifact's build timestamp, embedded once by Next at build time and
shared by all replicas of that artifact. Dates do not advance on each request.

The index has `loc` and `lastmod` for each sitemap file. Per-file content digests
preserve the modification date across refreshes when entries are unchanged.
The pages file incorporates publication dates and its template build. `priority`
and `changefreq` describe pages only; adding them to index entries would violate
the official index schema. They are crawler hints, not ranking guarantees;
[Google ignores these two hints](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).

## Cache and freshness

The public API stores sitemap inventories in shared Redis/Valkey, including
Sentinel deployments. All replicas use the same database-specific namespace.
The cache is independent of frequently invalidated reader responses and stays
enabled even if ordinary response caching is disabled.

`DEVFEED_SITEMAP_REFRESH_SECONDS` defaults to **900 seconds**. A shared lease
allows one builder to refresh an expired inventory, using streamed projections
in a read-only, repeatable-read PostgreSQL transaction. The builder writes every
part before atomically publishing the manifest. A warm index, pages sitemap or
part requires **zero SQL queries**. Ordinary reader page loads do not fetch
sitemap data.

Snapshot IDs are internal cache keys only. Public links never contain them and
resolve the current inventory. Historical `?v=...` URLs, including truncated IDs,
redirect permanently to the clean filename. Internal `/sitemaps/{kind}/{page}`
route aliases redirect to root-level filenames. Unknown kinds or absent current
parts return 404. There is no expiring public snapshot link to bookmark or submit.

Internal snapshots expire after approximately four refresh intervals plus five
minutes (65 minutes by default); child files have an extra minute of retention.
This bounds storage and protects concurrent readers during publication. A format
upgrade refreshes the inventory once. Old list-only cache entries remain readable
during a rolling application upgrade.

A failed refresh serves the previous compatible inventory until retention expires,
with a one-minute retry cooldown. A cold build or cache outage returns retryable
503 without bypassing Redis and scanning PostgreSQL on every request. Errors are
never publicly cached. Content visibility changes appear on the next refresh;
a recently withdrawn page can temporarily remain listed, but the page itself
continues to enforce current public visibility.

XML responses use weak ETags and conditional, bodyless 304 responses. HTTP/shared
cache lifetimes are 60 seconds for the index and 300 seconds for URL sets.

## Configuration and conformance

Set `DEVFEED_USER_BASE_URL` to the reader's canonical origin: `https://devfeed.tech`
in production, or the reachable local Compose origin. Request Host headers never
control advertised URLs. Outside Compose, an omitted origin defaults to production.
The web app contacts `DEVFEED_PUBLIC_API_URL`; sitemap JSON comes from `/v1/sitemaps`
and `/v1/sitemaps/{kind}/{page}`. No new service or database migration is required.

The XML follows the [Sitemaps 0.9 protocol](https://www.sitemaps.org/protocol.html)
and its official `sitemap.xsd` and `siteindex.xsd` schemas. This is not a
sitemap-specific IETF RFC. URI serialization and XML escaping are separate;
Unicode is percent-encoded before XML entity escaping. The namespace remains
`http://www.sitemaps.org/schemas/sitemap/0.9` even on HTTPS sites.

Validation enforces nonempty documents, at most 50,000 entries, full encoded URLs
shorter than 2,048 characters, and a maximum uncompressed size of 52,428,800 bytes.
The index reserves one entry for the public pages file. Same-origin URLs, valid
dates, priorities, path segments and unique part identifiers are checked before
XML is served. Root-level filenames allow all listed paths within sitemap scope.

Tests validate renderer output against unmodified official schemas using a test-only
libxml2 validator. Backend integration tests cover public visibility, publication
dates, stable modification times, partitioning, concurrent warm requests with zero
SQL, refresh failures and cache outages. HTTP checks verify actual Next rewrites,
legacy redirects, XML, page destinations, HEAD and ETag responses.

## Canonical page URLs

Public HTML pages declare an absolute `rel="canonical"` through Next.js metadata.
Metadata streaming is disabled so canonical tags are in the initial HTML head for
all clients, including crawlers that do not execute JavaScript:

- The homepage and all six content-type feeds.
- Topic, tag and source feeds, including their content-type routes.
- Topic and source directories, with distinct canonical URLs for later offset pages.
- Article previews, including the intercepted modal route.

A shared helper uses the configured public origin and the same route conventions
as public links and sitemap entries. Tracking and unrecognized query parameters
are omitted. Meaningful filters and pagination cursors remain in the canonical URL;
later result pages are not declared duplicates of page one. The existing noindex
policy for refined feeds, search and private pages remains in place. Tracking-only
variants use the clean canonical without adding noindex.

Legacy query-style topic/source/content-type links already redirect to their stable
routes; tag query links now do as well. Article UUID aliases retain their redirect
to the stored slug. Markdown representations use the same canonical policy in
HTTP Link headers, including the stored article slug and tracking-free feed URLs.
No homepage canonical is inherited through the root layout by private or missing
pages.

An article's HTML canonical is its **DevFeed preview URL**, matching its sitemap
entry. The API field `canonical_url` identifies the **original publisher's URL**
and remains the attribution/read-original destination. DevFeed serves its own
summary preview, not a full syndicated copy of that original article.

This follows [Google's canonicalization guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls):
use absolute canonicals, keep sitemap and page signals consistent, and consolidate
only duplicate or equivalent representations.

## Social previews and structured data

Public pages also emit Open Graph metadata for Facebook, LinkedIn, WhatsApp and
other compatible consumers, plus Twitter/X `summary_large_image` cards. Each
public route supplies its own title, description and Open Graph URL matching its
HTML canonical. The shared `/opengraph.png` artwork includes its actual dimensions,
MIME type and alternative text. Article previews use a valid HTTP(S) cover URL
when available, otherwise the shared artwork. No social account handles or Facebook
application IDs are assumed.

The root layout renders a Schema.org `WebSite` and `Organization` graph with stable
identifiers and the DevFeed logo. Feed, topic, tag and source pages describe their
visible results with `CollectionPage`, `ItemList` and `BreadcrumbList`. Directory
offsets are preserved. These graphs reuse data fetched to render the page; they
perform no additional API requests or database queries.

An article preview is a `WebPage` whose `mainEntity` is the original publisher's
`Article`. The local preview URL and original URL remain distinct. Author,
publisher, language, original publication date and illustration are included only
when available. Unknown publication dates and modification times are not invented,
and the DevFeed fallback artwork is not declared an original article illustration.
The preview description follows its visible overview. This original publication
date is separate from the **published to feed** date used by the sitemap.

JSON-LD is rendered on the server in native `application/ld+json` script elements.
Less-than characters in serialized content are escaped, preventing publisher text
from terminating a script element. Metadata stays in the initial HTML head without
requiring JavaScript. Private pages and search retain their noindex policy; the
source suggestion form is also noindex. Rich-result eligibility is determined by
search engines, not guaranteed by adding markup.

The homepage has a descriptive search title, and content-type subfeeds distinguish
themselves from their parent topic/source/tag pages in their titles. Public pages
permit large image previews. Existing server-rendered article links and the
infinite-scroll pagination links remain crawlable without JavaScript.

`robots.txt` blocks API crawling but permits HTML pages so crawlers can read their
`noindex` directives. Blocking those same pages in robots.txt would prevent that
instruction from being seen; [Google documents this distinction](https://developers.google.com/search/docs/crawling-indexing/block-indexing).
Account, authentication, source suggestion and search routes also send an
`X-Robots-Tag: noindex, nofollow` header, including redirect responses. Authentication
continues to protect private data independently of indexing controls.
