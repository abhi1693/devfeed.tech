# Public search markup

The reader emits server-rendered JSON-LD through `src/components/json-ld.tsx`.
`src/lib/structured-data.ts` owns page, collection, article and site entities.
Routes reuse their rendering data and the canonical helpers in `metadata.ts`;
markup does not make extra API requests. Do not add a parallel graph or a second
metadata system to satisfy a search feature.

## Page and feature matrix

This inventory was checked against the [Google Search gallery](https://developers.google.com/search/docs/appearance/structured-data/search-gallery)
on 2026-10-16. Structured data describes content; it does not guarantee a rich
result, indexing, or a particular search snippet.

| Public page | Markup | Google feature and policy |
| --- | --- | --- |
| `/` and `/latest` | One site `Organization`/`WebSite` graph; `CollectionPage`, rendered `ItemList` and breadcrumbs | Organization identity and Breadcrumb where a trail exists. The guest homepage canonicalizes to `/latest`; signed-in `/` is a noindex personal feed. |
| `/news`, `/articles`, `/tutorials`, `/releases`, `/comparisons`, `/opinions` | `CollectionPage`, rendered `ItemList`, `BreadcrumbList` | Breadcrumb. The ordinary article list is not a Google carousel. |
| `/topics`, `/sources`, including `?offset=60` | `CollectionPage`, visible directory entries in `ItemList`, `BreadcrumbList` | Breadcrumb. Later pages keep distinct canonical and entity IDs and their actual list offsets. |
| `/topics/{slug}`, `/sources/{slug}`, `/tags/{slug}` and their content-type subfeeds | `CollectionPage`, rendered `ItemList`, `BreadcrumbList` | Breadcrumb. Existing empty/refined-feed noindex policies still apply; missing collections remain 404. |
| `/articles/{slug}` | Local `WebPage` with original publisher's `Article` as `mainEntity`, a local AI overview `CreativeWork` when present, plus `BreadcrumbList` | Article and Breadcrumb markup. DevFeed publishes the preview and overview; original publisher attribution and article URL stay distinct from the local canonical. Google decides eligibility for the preview. |
| `/legal/terms`, `/legal/privacy` | `WebPage`, visible update date, `BreadcrumbList` | Breadcrumb. Legal text is not an Article, FAQ, or how-to. |
| `/mcp` | `WebPage`, `BreadcrumbList` | Breadcrumb. Setup instructions do not make this a software listing or educational course. |
| `/users/{username}` | Site identity only; existing social metadata and `noindex, follow` | ProfilePage deferred. Keep profiles out of search and the sitemap. Visibility-filtered public sharing does not imply consent to search indexing. |
| `/dev-card` | Site identity only | Card creation is a utility, not a creator profile or gallery feature. |
| `/leaderboard`, `/trending` | Site identity only; existing noindex policy | No ranking, review, or rating feature. Reading statistics are not product reviews. |
| Search, settings, read-later, personal feeds, login, consent, source suggestion, extension completion | No new feature graph; existing authentication/noindex controls | Not search-feature targets. An account endpoint is never a source of public profile markup. |
| API, Markdown, images, sitemap XML, browser extension documents | No HTML feature graph added | Representations and application surfaces, not separate indexable rich-result pages. Chrome and Edge keep the shared reader behavior. |

ProfilePage requires an indexable page focused on an affiliated person or
organization. Before enabling it, make a separate product/privacy decision about
explicit indexing consent, account visibility and deletion behavior, supported
public identity fields, canonical URLs and sitemap inclusion. Do not infer an
original article author's identity from a DevFeed reader account. See Google's
[ProfilePage guidance](https://developers.google.com/search/docs/appearance/structured-data/profile-page).

The remaining gallery features are excluded unless DevFeed starts publishing the
qualifying content and verified facts: products/merchant policies and offers;
software listings with their required offer/review data; jobs and employer
ratings; local businesses and vacation rentals; events; books/movies; recipes;
courses and education Q&A/math solvers; datasets; discussion forums/Q&A; and
licensed image metadata. Article links about these subjects do not qualify the
DevFeed preview for those features. DevFeed does not offer Speakable news passages
or a paywall for these previews.
No ratings, prices, availability, author profiles or licensing terms are invented.
Google's host [carousel](https://developers.google.com/search/docs/appearance/structured-data/carousel)
requires a supported content feature; an ordinary feed/directory `ItemList`
does not establish eligibility.

## Properties and identity

- [Organization](https://developers.google.com/search/docs/appearance/structured-data/organization)
  has no required properties. DevFeed supplies its name, URL, description and
  crawlable PNG logo with stable ID and actual image dimensions. The logo meets
  Google's 112 by 112 pixel minimum. The site's publisher references this same
  entity. Do not invent a legal name, company identifier, address, phone or social
  identity. Root-layout integration provides one site graph per document.
- [Article](https://developers.google.com/search/docs/appearance/structured-data/article)
  has no required properties. Recommended headline, original publication date,
  image and author name are emitted when known, alongside source attribution.
  The local preview description follows its visible overview and matches HTML
  and social metadata. The original article description follows the source excerpt.
  Original headlines are separate from DevFeed's display titles and are emitted
  only when their provenance is known. Publisher identity requires one approved
  publisher origin matching the original website; discovery-source order does not
  establish publication identity. Unknown `dateModified` and author profile URLs are omitted;
  feed discovery/publication time is never substituted for an original date.
  An original cover is optional; the DevFeed social fallback is not an article
  illustration. Missing author/image/date warnings are assessed as missing source
  facts, not filled with guesses. Existing untyped bylines remain visible but do
  not become inferred `Person` identities. Only explicitly typed author data is
  eligible for structured author attribution.
- A nonempty AI overview is a local DevFeed-published `CreativeWork`, linked from
  the page with `hasPart` and to the original with `isBasedOn`. Its full English
  Markdown source is preserved as `text`, with `encodingFormat: text/markdown`.
  It does not inherit the original article's byline or publication date. A source
  excerpt without an AI overview is not claimed as a DevFeed-created work.
- Article tags supply the HTML `keywords` metadata, Open Graph article tags, and
  local page/original article JSON-LD `keywords`. No extra keywords are generated;
  articles without tags omit keywords metadata.
- [Breadcrumb](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb)
  requires at least two ordered `ListItem` entries with positions and names, and
  an item URL for every non-final entry. The shared page helper supplies absolute
  URLs for every entry, including the final page, and stable breadcrumb IDs.
  Trails follow reader navigation: Home, optional Topics/Sources directory, page.
  Article trails include the same source link shown in the preview, preferring
  the verified publisher when available and a discovery source otherwise.
  The root alone has no one-item trail. Breadcrumbs are navigational context,
  not an invented content taxonomy.

Local page IDs use the local canonical plus `#webpage`, `#breadcrumb`, or `#items`.
The original Article ID/URL remains the publisher's URL. Do not claim that DevFeed
wrote it or that its original `mainEntityOfPage` is the DevFeed preview. JSON-LD
uses the shared serializer, which escapes `<` so publisher text cannot terminate
a script element. Private/noindex content is not made eligible by the presence
of the site-wide Organization graph. Follow Google's
[general structured-data guidance](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data).

## Validation and deployment

Run from the repository root:

```sh
npm run web:test -- tests/structured-data.test.tsx tests/routes.test.ts
npm run web:build
node apps/web/tests/browser/structured-data.mjs
npm run extension:build:all
node --test --test-name-pattern='reader routes validate' apps/extensions/tests/reader.browser.mjs
DEVFEED_EXTENSION_BROWSER=edge node --test --test-name-pattern='reader routes validate' apps/extensions/tests/reader.browser.mjs
```

The crawler/browser test checks canonical/entity identity, one site and page graph,
attribution, breadcrumb properties, pagination and profile noindex behavior on
the built application. Raw HTML, test summaries and optional code-test inputs
remain under ignored `reports/structured-data/`; they are local fixture evidence,
not evidence of a deployed change or a Google validation pass.

Before merging or after staging, submit representative rendered HTML in **Code**
mode to the [Rich Results Test](https://search.google.com/test/rich-results).
Target Organization, Article and Breadcrumb, check for critical errors and assess
warnings against the available source facts. Code mode checks markup; it cannot
prove that production URLs, images or robots policies are crawlable. During this
implementation, the test UI loaded but code submission returned “Something went
wrong — Log in and try again”; no successful Google result is claimed.

After deployment, use **URL** mode and Search Console **URL Inspection → Test live
URL** on these representative URLs:

- `https://devfeed.tech/latest`
- `https://devfeed.tech/topics` and `https://devfeed.tech/topics?offset=60`
- `https://devfeed.tech/sources`
- `https://devfeed.tech/articles/android-studio-rabbit-1-now-available-63523`
- `https://devfeed.tech/legal/privacy` and `https://devfeed.tech/mcp`

Also select an active topic, source and tag URL from the current sitemap; add a
content-type subfeed and an article without optional author/image/date data.
Verify fetched HTML, canonical selected by Google, robots/indexability, visible
content, original attribution and image/logo access. Check a consenting test
profile stays `noindex` and emits no ProfilePage graph. Submit the sitemap and
request indexing for changed eligible pages. Monitor Breadcrumb and Unparsable
structured data reports, any applicable enhancement reports, and Article search
appearance/performance when exposed; not every schema has its own report. Inspect
Organization identity through live markup/URL Inspection rather than assuming an
Organization enhancement report exists. Compare valid/invalid item counts after
recrawling; allow indexing time and verify actual search snippets separately.

This markup does not itself resolve duplicate titles or search engines choosing
unrelated snippet text. Those are tracked separately in #161 and #162.
