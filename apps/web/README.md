# Public user

Anonymous Next.js website on port 3000. Includes the newest-first article feed,
URL-based search and filters, topic and source directories, and article previews
with original-publisher links. Public browsing uses the anonymous API. Optional sign-in, followed topics, and
My feed use the separate user API through a same-origin gateway.

Guest homepage entry renders the public feed directly at `/` without a document
redirect. Both `/` and `/latest` default to **All** content types. Selecting
**Articles** restricts the feed; selecting **All** removes that restriction. Query filters, cursors
and campaign attribution remain in the entry URL. Canonical metadata points to
the established public feed routes; verified sessions still receive private My
feed metadata and content. Chrome and Edge use the same guest defaults and keep
root entry in place after session detection.

For guests, both entry points canonicalize to `/latest`. The homepage search title
is "Developer news, tutorials & releases"; `/latest` uses "Latest developer news,
tutorials & releases" in HTML and social metadata. After deploying title changes,
recrawl HTTP and HTTPS entry points and check their titles and shared HTTPS
canonical in Search Console and Bing Webmaster Tools.

After a production build, run `node apps/web/tests/browser/home-entry.mjs` for
entry, query, canonical and session regression checks. Set
`DEVFEED_ENTRY_BENCH=before` or `after` to capture three paired root/direct-latest
Lighthouse mobile runs with fresh Chrome profiles, default simulated mobile
throttling and a deterministic local API fixture. JSON/HTML reports and screenshots
stay under ignored `reports/home-entry/`. Compare builds using the same browser,
Lighthouse version and fixture; these lab measurements are not field p75 data.

New-account topic selection displays each page as it arrives. Each request has its
own timeout; a later failure preserves loaded topics and selections, and retry
resumes at the failed page. Saving or closing the dialog stops catalog loading.
The same onboarding component and browser regression run in the website and both
browser extensions.

My feed shows a bounded recent-article starter page while the first personalized
ranking is prepared. Direct follows are preferred; a latest-articles fallback is
labelled separately. Both obey language, content-type, source and publication
filters. Existing lists remain readable during refresh or transient errors;
readers choose **Show updates** when a new list is ready. Preparation checks run
every three seconds initially, then every five seconds, pause in hidden tabs,
and resume immediately when visible. A delayed message appears after ten seconds
or when the API reports a retry, with a latest-feed link available throughout.
These behaviors share code and browser tests with Chrome and Edge.

From the repository root, run `npm ci` then `npm run web:dev`. Set
`DEVFEED_PUBLIC_API_URL` to the API origin (default `http://127.0.0.1:8000`).
For user sign-in, also set `DEVFEED_USER_API_URL` and
`DEVFEED_USER_BASE_URL`. See [user accounts](../../docs/user-accounts.md).
Set `DEVFEED_MCP_PUBLIC_URL` on the web service to the full MCP ingress URL to
populate the reader’s `/mcp` setup page, for example `https://mcp.example.com/mcp`.
This setting is read at runtime and is also exposed to Chrome and Edge through
`GET /api/v1/mcp/config` with `Cache-Control: no-store`. There is no guessed URL or
localhost fallback when it is unset. See the [MCP guide](../mcp/README.md).

Run `npm run web:lint`, `npm run web:test`, and `npm run web:build` to validate.
After user API route or transport-policy changes, run `npm run web:generate` and
include the OpenAPI, operation definitions and concrete Next handlers together.
The same command generates extension transport policies from user API metadata and
the public reader bindings in `src/lib/api/reader-bindings.json`.
`node apps/web/tests/browser/reader-gateway.mjs` checks the built gateway over real
HTTP, including cookie isolation, placement validation and body-reading deadlines.

Server JSON reads use `PublicApiClient` or `PrivateApiClient` from
`src/lib/server/api-client.ts`. The public client binds the configured public API
origin and requests `max-age=600` upstream, while Next fetch storage uses
`no-store`. The private client binds the user API origin, requests `no-store`
even for anonymous reads, and forwards only reader cookies. Select a client by
operation ownership. Endpoint names do not select credentials or cache policy.
Sitemap inventory uses a public client configured for fresh reads and a
55-second deadline.

The shared reader destination definitions declare sitemap inclusion, priority,
change frequency, and publication metadata. Legal pages also take their titles,
descriptions, paths, and update dates from those definitions. Sitemap handlers
serialize declared destinations and serve the backend's retained inventory.

See [public search markup](SEARCH.md) for the page-to-feature matrix, structured
data identity and attribution rules, profile indexing policy and post-deployment
Search Console checks.

After building web, Chrome, and Edge, run
`node --test --test-name-pattern="server reads" apps/web/tests/browser/feed.mjs` or
`node --test --test-name-pattern="server reads" apps/extensions/tests/reader.browser.mjs`
to check public/private reads and sitemap responses. Set `DEVFEED_EXTENSION_BROWSER=edge` for Edge.
The web checks record actual server requests to separate local upstreams;
browser screenshots stay under ignored `reports/server-reads/`.

On the public `devfeed.tech` host, requests forwarded by Cloudflare with
`X-Forwarded-Proto: http` receive a permanent redirect to the same HTTPS path
and query. HTTPS responses include HSTS and a per-request script nonce.

See [development](../../docs/development.md) and [Compose](../../docs/compose.md)
for deployment and runtime configuration.

The header sun/moon control switches light and dark themes without sign-in. A
small script applies the saved `devfeed:theme` local preference before rendering;
otherwise the site follows the operating system. Changes synchronize across tabs,
and the toggle remains usable when local storage is blocked.

Temporary article API 502/503/504 responses retain the article dialog with retry
and close controls instead of raising an unhandled page error. Temporary pages
are marked noindex; missing articles still return 404 and unexpected exceptions
still reach the error boundary. Chrome and Edge share the error content and retry
control. The browser suites exercise failure followed by successful retry.

Direct article pages keep the requested article's title, overview, canonical and
structured data together. Their streamed background feed and related sidebar
carry `data-nosnippet` so unrelated text is excluded from Google search snippets.
The loading fallback and resolved feed segment both carry the exclusion; the
article overview stays eligible. The shared sidebar also applies in Chrome and
Edge. This does not hide content or prevent link crawling. Search engines choose
their own snippets; after deployment, use Search Console URL Inspection and Bing
Webmaster Tools to request a recrawl and verify the indexed result.

Reader cards and inbox actions pass `articleDestination(slug)` to navigation;
page actions use `pageDestination(href)` with their scroll policy. `ReaderLink`
serializes these destinations for native links, and `useReaderRouter` forwards
them through the website or extension adapter. Requested article identity and
overlay ownership come from the registered Next route slots or extension history
state, including while the article is loading or unavailable. Preview components
do not recover that state from URL strings. Extensions retain the resolved
background page across preview navigation and browser history.

After building web, Chrome, and Edge, run
`node --test --test-name-pattern="reader navigation" apps/web/tests/browser/feed.mjs` or
`node --test --test-name-pattern="reader navigation" apps/extensions/tests/reader.browser.mjs`
for the navigation journey. Without a name filter, every named scenario runs with its own fixtures.
Use `DEVFEED_EXTENSION_BROWSER=edge` for the Edge run. It checks retained feed
DOM and scroll, direct and intercepted previews, Back/Forward, retry, and inbox
article and settings navigation. Evidence stays under ignored
`reports/reader-destinations/`.

## Reader loading and pagination

Reader pages must not exhaust a catalog or feed to render the first screen. The shared web reader and Chrome/Edge extensions use the same incremental choices and scrolling controls.

| Surface                             | Initial work                                                                                       | Continuation                                                                          |
| ----------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Your topics / Your sources          | Render account shell; after authentication and saved preferences, request one 60-item catalog page | Scroll requests one cursor; name search starts a new server-filtered page             |
| My feed onboarding                  | Check saved topic IDs; eligible readers request one ranked topic page                              | Scroll within the dialog; server-filtered name search preserves selections            |
| Public topics / sources             | One 60-item directory page                                                                         | Scroll fetches the next page                                                          |
| Latest and filtered feeds           | One feed page, bounded topic suggestions, and filter options                                       | Feed cursor requests on scroll                                                        |
| Topic/source detail feeds           | Direct item lookup, then one feed page and bounded supporting reads                                | Feed cursor requests on scroll; extensions never scan directories to resolve a slug   |
| Article preview                     | One article lookup and its featured topic lookup                                                   | No catalog pagination; extension cached-article fallback uses one direct topic lookup |
| My feed / Read later / Trending     | One feed page                                                                                      | Cursor requests on scroll; My feed refreshes its first page when active               |
| Search                              | One search response                                                                                | Independent section cursors on scroll                                                 |
| Public Markdown catalog/feed routes | One bounded page                                                                                   | Next-page links, without draining catalogs                                            |

Search's Results filter distinguishes Articles, News, Tutorials, Releases,
Comparisons, and Opinions. All article types searches those six types together;
All results also includes topics, sources, and tags. Type choices use the public
API's `content_types` filter and remain selected through sorting, date changes,
shared URLs, and pagination on web, Chrome, and Edge.

The settings server components do not preload catalogs for signed-out visitors. Saved topic/source IDs are loaded independently of visible catalog rows; saving a partially loaded selection must retain IDs outside that page. Search is debounced and applied by the public API before offset/limit, so it finds entries that have never been downloaded. Matching is case-insensitive literal name containment, including literal `%` and `_`. Existing publication, approval, enabled-source and active-topic filters still apply.

Requests have deadlines, cancel on unmount/inactivity, and deduplicate cursor loads. Replacing a search unmounts its page state and cancels the prior request. Failed pages retain already-loaded items and expose a manual retry; they do not start an automatic retry loop.

### Validation and deployment

Shared browser regression helpers test preferences, public directories, onboarding, feed/search scrolling, and article/detail navigation against the production web build and both built extensions. Preference checks record catalog requests to reject eager later-page fetches, search for an unloaded item, and retain selection after clearing search. Backend integration tests use disposable PostgreSQL and Redis to verify global search, pagination, literal wildcards, and public visibility rules.

The public API adds the optional `q` parameter to topics/sources. Deploy that API before the corresponding reader/extension release: an older API ignores `q` and cannot provide correct global catalog search. New same-origin topic/source detail endpoints must also be live before distributing the updated extensions. No database migration is needed. These source changes and local validations do not prove a production latency improvement until a separately authorized deployment is measured.

## Partner placements

Product ad cards show the import connection name beside the platform icon and optional
attribution (for example, "Via Nick Launches") in the bottom row. Empty attribution
leaves that row empty with its space reserved. Compact ads also show attribution in their footer; header ads include
it before the tagline. Delivery uses the selected account-owned source, honoring its
current connection attribution immediately, including when it is cleared. Legacy
connections without that setting retain their imported listing attribution.

The shared web, Chrome and Edge reader expires product placements one hour after
their tracking receipt was issued. A timer and tab-resume checks enforce the
deadline while the page stays mounted. Expired feed cards become hidden, inert
cells with the same dimensions, preserving article position and focus. Placements
outside feed slots are hidden. The same reading context does not fetch replacements;
navigation to a new context can request a fresh delivery. Tracking checks the
deadline before impressions, clicks and retries, including after suspended timers.

After building the web app and both extensions, run
`node apps/web/tests/browser/partner-placements.mjs` and
`node apps/web/tests/browser/partner-tracking.mjs` for browser parity checks.
Use `node apps/web/tests/browser/partner-placements.mjs --attribution` for the
focused attribution checks and light/dark desktop/mobile screenshots.

## Direct article entry

Direct article URLs stream the requested preview before the optional background feed.
The server-open dialog is readable without the reader bundle; a small response-nonced
script enables native modality immediately. Hydration preserves that modal instead
of closing and reopening it, and direct entries skip the opacity entrance animation.
Escape works before hydration too. Intercepted feed previews retain their animation,
focus restoration, history and scroll behavior, including in Chrome and Edge.

`tests/browser/article-first.mjs` holds every external application bundle while
checking summary/error visibility, native focus containment and scroll locking.
The web feed suite also checks Escape with those bundles unavailable. Extension
reader suites exercise the shared native-modal and navigation behavior.

For five serial mobile Lighthouse runs against a production build:

```sh
node scripts/ci/browser-performance.mjs --article-entry=before --article-fixture=/tmp/article.json
node scripts/ci/browser-performance.mjs --article-entry=after --article-fixture=/tmp/article.json
```

Use identical public `{ "article": ... }` JSON snapshots and baseline/fixed source
for each phase. The harness retains the snapshot, raw JSON/HTML and summaries under
ignored `reports/article-entry/`. It uses local deterministic cover/icons, anonymous
storage and Lighthouse mobile defaults (412×823, DPR 1.75, simulated mobile network,
4× CPU). These controlled results do not prove deployed production latency; measure
the public URL again after a separately authorized rollout. Default CI remains
three runs across all six reader pages.

## Reader invitations

The signup invitation appears after three distinct article routes, once sign-in
status has resolved. **Not now** dismisses automatic account invitations for the
session, including the anonymous Dev Card promotion. Dismissing an anonymous Dev
Card also silences the signup invitation. The banner sits above mobile navigation,
so readers can still change views without dismissing it. This reuses
`devfeed:dev-card-promo-dismissed`, so existing refusals stay respected across
navigation and reloads. Signed-in Dev Card dismissal remains account-specific.
When session storage is blocked, the mounted reader keeps dismissal in memory;
it cannot persist that refusal through a full reload without browser storage.

On the website, Chrome and Edge, topic onboarding takes priority while preferences
load. Invitations wait behind it and native article or
account dialogs; an active signup invitation also defers the automatic Dev Card.
Closing or completing topic onboarding pauses automatic invitations for 30 seconds.
Explicit **Preview your card** actions bypass that pause and session refusal, and
can replace a signup banner, while still waiting for an existing modal to close.
There is one active invitation/onboarding prompt per reader. The coordinator
releases it on unmount and restores the existing dialog scroll behavior.

`tests/browser/signup-prompts.mjs` runs the dismissal and coordination journeys
against the website and both built extensions; `feed-onboarding.mjs` also checks
onboarding priority and the pause. Review screenshots stay in ignored reports or
extension build output.

## Article preview covers

Article previews reserve a responsive 16:9 cover frame, up to 420px wide, before
loading publisher images. Images retain their original proportions with
`object-fit: contain`; square and portrait images have space around them rather than being cropped.
Failed images use the same frame for their placeholder, keeping the preview layout stable.

Shared browser regression coverage in `scripts/testing/preview-cover.mjs` exercises
delayed landscape, square, portrait, and failed images at mobile and desktop sizes
on the website and both built extensions. Reports and screenshots stay untracked in
`reports/preview-cover`. Every run asserts reserved dimensions, stable surrounding layout,
and Lighthouse's `unsized-images` sizing classification using the authored CSS.
These deterministic fixture reports are not full production Lighthouse audits.

## Dev card signup preview

Anonymous feeds and signed-in feeds whose reader has not claimed a username show
the same dev card modal after at least 30 seconds on the page, waiting
for other open dialogs to close. The card reveals first; after 1.9 seconds, the modal
expands and its details slide in on the right (below the card on mobile). The
popup does not change the feed layout. Client-side navigation keeps the elapsed
time; a full reload starts it again. The shared web/extension component rotates
and zooms the card each time the popup opens, with a light sweep, glow, brief
floating motion, and mouse tilt. Reduced-motion preferences disable movement, and readers
see the automatic promotion only once per browser or extension installation. Its first display
is stored in local storage, even when the reader leaves without dismissing it. Clearing
site data resets this record; separate browsers and devices have separate records.
If persistent storage is unavailable, the automatic promotion stays hidden. Explicit
previews on `/dev-card` remain available. Readers can dismiss the open promotion.
Example statistics are labelled and disappear when personalizing the preview.

Signed-in readers see “Finish your dev card,” which opens Your Dev Card. The
promotion waits for their profile to load and stays hidden if it cannot be loaded
or already has a username, including private profiles. A saved preview draft can
still be finished after sign-in. Dismissal lasts for the session and is remembered
separately for anonymous visitors and each signed-in account.

Visitors can preview a display name and up to four catalog technologies before
registering. The draft stays in the original tab's session storage for up to 24
hours. Web signup returns to Your Dev Card; extension signup uses the existing
completion tab and offers “Finish your dev card” in the original tab. Your Dev Card
restores the draft for review and explicit saving. The account menu, settings
navigation, and page title use this name for the editor at `/settings/profile`.
The account menu offers Your Profile above Your Dev Card, linking to
`/users/<username>` once a username is claimed and the profile is public. Until
that link is available, Claim your username opens the editor at `/settings/profile`.
This flow does not automatically make profiles public and requires no migration
or configuration.

To replay an automatic reveal locally, clear `devfeed:dev-card-promo-seen` from
local storage, then clear `devfeed:dev-card-promo-dismissed`
from session storage and reload an anonymous feed (or clear the same key suffixed
with `:<user_id>` for a signed-in account). Shared Playwright coverage lives in `scripts/testing/dev-card-promo.mjs`,
invoked by the web feed browser test and the Chrome/Edge auth browser suites.
Screenshots are saved in `reports/reader-feed` and `apps/extensions/dist`.

The visual preview remains usable when authentication is unavailable, including
a local stack without OIDC configuration. Saving and signup are disabled with
an explanation until authentication is available.

### Public card sharing

Profile visibility defaults to public when no preference is stored. An explicit
private preference remains private and the checkbox can be unchecked before saving.
Saved profiles with a username and public visibility enabled can copy their card
link with **Copy Link** beside the download controls. The Markdown embed field below
provides a linked card image for GitHub READMEs and other Markdown pages. Extension links use
the website origin and open the public card in a browser tab.

`/users/{username}` is the public profile: identity, bio, links, about text,
technology groups, reading statistics, and a UTC activity calendar. Card previews,
sharing controls, and Markdown embeds stay in Your Dev Card. It uses only unauthenticated public
profile and heatmap responses. Empty optional sections are omitted.

The standalone card endpoint `/api/v1/users/{username}/card.svg` returns only SVG
artwork, with no HTML page or navigation. It shares the browser card's SVG frame,
technology icons, brand asset, and theme tokens. Text wrapping is computed on the
server. Sanitized avatars and technology logos from the bounded process cache are embedded directly;
cache misses use initials and readable technology names while safe, bounded image
fetches warm the process cache for later requests. Avatars are resized before
embedding. The SVG never references remote images and requires no session. Example:

```markdown
[![DevFeed card](https://devfeed.tech/api/v1/users/reader/card.svg)](https://devfeed.tech/users/reader)
```

The profile's social preview is a 1200×630 PNG at `/users/{username}/image`.
All public profile/image routes and upstream requests disable caching. Missing or
private profiles return 404. External image proxies and social networks may retain
images they have already fetched. SVG assets are explicitly included in standalone
build tracing; no runtime browser or remote screenshot service is required.

Your Dev Card lets users choose which current/learning technologies and reading
streak stats appear on their Dev Card. The live preview updates before saving; saved
choices are used by the public profile card, SVG embed, and social preview image.

“Create yours” leads to `/dev-card`, where an explicit preview action opens the
existing card editor immediately, even if the automatic promotion was dismissed.
The automatic feed promotion still waits at least 30 seconds. Signup keeps the
draft in the current tab for restoration in Your Dev Card.

Coarse analytics events cover `dev_card_view`, `dev_card_create_click`,
`dev_card_preview_started`, `dev_card_signup_started`, `dev_card_saved`, and
`dev_card_share` (link, Markdown, or download). These events carry no profile
fields or account identifiers. Signup-start events measure intent, not completed
registrations; saved-draft events mark completion of the card flow. No migration
or new environment setting is required.

## Embeddable Dev Cards

`/api/v1/users/{username}/card.svg` returns a self-contained SVG for Markdown and
external image embeds. Cached avatars, topic logos and the DevFeed mark are embedded
as data URLs; uncached images use initials or topic names and are fetched for future
requests without delaying the current response. An SVG loaded as an image never
relies on external nested images.

Topic logo downloads reuse the avatar transport's public IPv4/DNS pinning,
redirect validation, 2 MB response limit and timeout. Up to four distinct logos
warm concurrently within a shared five-second deadline. Sanitized image results use
a bounded in-memory cache; Sharp decodes avatars to 256px PNGs and raster or SVG logos
to transparent 96px PNGs, with input pixel and processing-time limits enabled.
Undownloadable or invalid logos render the topic name. No remote SVG markup enters
the card document.

Browser PNG exports also reveal the topic name if CORS or image loading prevents
embedding a logo. The shared export code is exercised in web, Chrome and Edge
browser tests. The embed route itself belongs to the web service; extensions use
that same public URL when sharing a card.

The visitor modal features `asaharan` using the existing public, visibility-filtered
profile endpoint. Web and extensions use the same reader transport and artwork.
No featured profile fields or reading statistics are hardcoded. Unavailable/private
profiles show a neutral message while card creation remains available. Starting
or restoring a visitor draft replaces the featured card with their own preview.

Public profile payloads use the shared Redis cache for six hours, configured with
`DEVFEED_CACHE_PUBLIC_PROFILE_TTL_SECONDS=21600`. Profile saves and topic changes
invalidate cached cards immediately; reading statistics refresh on expiry. Every
request still checks account existence and public visibility, and browser responses
remain `no-store`. Redis outages fall back to normal profile loading. This applies
to the anonymous sample, public cards and exports in web, Chrome and Edge; private
profile settings remain uncached.

Dev Card design settings offer Classic, Terminal, Aurora, and Minimal themes with
a theme default or five accent colors. Design choices persist in the existing
profile JSON; no database migration is required. The shared theme package owns
the palettes, and the reader preview, PNG export, SVG embed, and social image
use the saved selection. Changing card content preserves the selected design.

The saved `dev_card.motion` setting defaults to `animated`. Animated cards use
script-free CSS inside the SVG to animate only decorative artwork: scattered dots
fading independently, circuit signals, layered aurora drift, or expanding rings.
Explicitly saved Static choices remain static. Embedded SVGs include
the same motion rules and honor `prefers-reduced-motion`. Hosts that strip SVG
styles may show a static card. PNG exports remove the animation style and use
a deterministic static composition; social preview PNGs also remain static.

`DEVFEED_MCP_PUBLIC_URL` is the single URL for public and OAuth account tools.
The expandable **Sign in to DevFeed** setup step shows the selected client’s
authentication action, browser consent, and how to verify account access. Codex
includes a copyable `codex mcp login devfeed` command. Agent setup prompts include
the same sign-in instructions. Account tools prompt for browser sign-in and consent.
Signed-in users can use the third **Connected agents** tab to revoke access.
The reader and both extensions share these
controls. `/mcp/authorize` is a private, non-indexed consent page that preserves
the pending request through normal DevFeed sign-in. See `apps/mcp/README.md` for
OAuth ingress routes and user-service/Redis configuration.

### Article views

Use the Grid and List icons beside Filters to switch layouts without resetting the
loaded articles, order or pagination. The same choice applies to My feed, Latest,
topic/source feeds, Trending and Read later; feeds without filters show the control
in their heading. Grid is the default. Guests keep their choice in this browser,
and signed-in readers save it to the existing feed preference for their account.
Content and language selections stay unchanged. Failed saves restore the previous
layout with a retryable message.

List displays compact rows with article titles, source, publication date and article
actions. Narrow lists keep source/date details beneath the title and a 44px bookmark
target, while omitting secondary engagement controls and topic links. Layout changes
retain loaded engagement data and the article preview's background list.

### Responsive article cards

The shared web and extension card grid chooses columns from the available feed width,
with a 280px minimum card width (or one column when the feed is narrower). Rows grow
with the feed. Cards use an inline-size container query: at 340px of inner card width
or less, the footer shows the date and bookmark and hides likes and open counts.
The article preview and read-feedback overlay retain their engagement controls.
Phone card titles use 16px text with a 1.4 line height and a three-line limit.

`npm run reader:test:parity` includes the shared `scripts/testing/article-grid.mjs`
browser checks on the website and built Chrome and Edge extensions. The matrix spans
16 viewport widths from 320px to 2560px, both sidebar states, light and dark themes,
long titles, unbroken topic names, long publisher names, and large engagement counts.
It checks column adaptation, overflow, title space, footer fit, 44px bookmark targets,
and the container-query boundary. Screenshots remain in ignored reports/build output.

## Next.js 16.4 tooling

The app uses Next.js 16.4 and React 19.3. The Rust React Compiler optimizes
component rendering with Turbopack. Cache garbage collection, lazy client dynamic
imports, and worker-thread plugin execution are enabled for development/builds.
Next.js may fall back to child processes on Node.js versions affected by its
worker-thread compatibility checks. Upgrade reminders use the `latest` policy.

Run `npm run web:analyze` from the repository root for the interactive Turbopack
bundle analyzer, or `npm run web:analyze -- --output` for an offline report under
`apps/web/.next/`. Analysis does not replace a production build. Use the analyzer
to compare route sizes and identify large dependencies before making changes.
Save a named capture with `npm run web:analyze -- --output --snapshot baseline`
and compare subsequent captures in the analyzer.

Cache Components and Partial Prefetching remain disabled: both apps use a fresh
script nonce per response, and Next.js static shells are incompatible with this
[CSP model](https://nextjs.org/docs/app/guides/content-security-policy).
Consequently, `ensureStatic`, `navigation()`, and `prefetch()` are not applied to
these request-rendered routes. Adopting them requires a compatible CSP design
and verification of runtime settings, authentication, and personalized feeds.

Validate with `npm run web:lint`, `npm run web:test`, and `npm run web:build`.

Public `/latest`, `/sources`, and `/topics` documents stream the shared navigation
and an accessible loading state while public results load. Listing boundaries do
not wrap detail routes: missing or hidden topics/sources still return HTTP 404.
Feed redirects and session checks finish before the feed boundary. Keep one
`UserShell` outside the results boundary and pass `withShell={false}` to the
feed/catalog content inside it. The extension adapters use the same structure,
so loading completion preserves search drafts, focus, and text selection. Canonical
metadata remains in the initial head, and HTML retains fresh CSP nonces and
`no-store`. Public reads continue using the API's generation-validated cache;
there is no additional web data or HTML cache.

After a production build, `node apps/web/tests/browser/document-streaming.mjs`
checks blocked API reads, retained search drafts/focus/selection, metadata,
per-user preferences, visibility changes and HTTP status codes. For read-only latency profiling, set
`DEVFEED_DOCUMENT_API_ORIGIN` to a public API origin and run
`node apps/web/tests/browser/document-latency.mjs`. Optionally set
`DEVFEED_DOCUMENT_PHASE=before` or `after`; reports stay under ignored
`reports/document-latency/`. The profiler records the first document byte and
completion time, upstream duration/count/cache status, and initial canonical
metadata. Each path gets a new Next process and five warm repeats. Process-cold
and API-cache-cold are distinct: inspect the recorded upstream cache status.
Streaming improves the first response; it does not promise faster API queries or
completion. Local results do not establish production Lighthouse or field p75.

## Profile avatar delivery

Uploaded WebP avatars retain their fixed account object keys, revision queries, and
revalidation policy so replacements and deletions remain effective. Profile and visibility
responses remain uncached in browsers; image caching never makes a private profile public.

The shared reader optimizes only `https://avatars.githubusercontent.com/u/NUMERIC_ID`
avatars with supported numeric `v`/`s` parameters. Its `/api/avatars/github/ID/SIZE` endpoint
constructs the upstream URL, uses the existing public-DNS-pinned, byte/pixel/time-bounded
image fetcher, and emits metadata-free WebP at 32/64/96/128/192/256/512px. Other selected
URLs remain unchanged; managed uploads take precedence. Packaged readers use the website
origin for this endpoint. An unavailable image falls back to initials.

GitHub images have a bounded one-hour browser/CDN cache with content ETags, rather than
immutable caching because GitHub can change artwork at the same URL. The existing bounded
in-process image cache lasts 30 minutes; provider changes can therefore take up to 90 minutes
to propagate through both caches. Replacing a selected URL or its version changes the cache
key immediately. Upstream work coalesces, with at most 16 active requests and 64 queued
requests. Queue waiting and fetching share the caller’s timeout budget; expired waiters
leave the queue, and shared callers retain their own deadlines. Queue expiration and
requests beyond the bound are not cached, so a later attempt can succeed.

Run `node tests/benchmarks/avatar-delivery.mjs` after a web build to compare current GitHub
PNG delivery with the actual optimized endpoint at mobile/desktop DPRs. Downloaded artwork,
reports and screenshots stay in ignored `reports/avatar-delivery`.

## Back and Forward

See [reader history restoration](HISTORY.md) for the session and visibility guards,
BFCache blocker assessment, and reproducible web/Chrome/Edge comparison. The
browser regressions run in `npm run ci:reader-parity`.

## Reader startup measurements

Profile links use content-hashed local SVG masks instead of shipping the full brand-path
catalogue as JavaScript, including GitHub and X. Each displayed brand makes a cacheable local
asset request. Chrome and Edge builds copy the same assets into each package;
no third-party favicon lookup occurs. Run `node scripts/assets/profile-icons.mjs` after
updating Simple Icons, then remove unused old asset files and commit the updated map/assets.

Operational browser telemetry retains its hydration-time initialization, error/event collection,
sanitization, disabled/DNT guards and 10% session sampling policy. Tracing code loads only after
a session is sampled, including later session changes; sampled tracing begins after its
asynchronous import completes.
Passive profile-view analytics are queued until interaction or page exit, retaining the original
public pathname without query/hash. The queue is bounded to 20 views; first-action events still
configure analytics and flush queued views before sending the action. Clarity retains its
existing first-interaction behavior.

Build the website, then run `DEVFEED_STARTUP_LIGHTHOUSE=1 node tests/benchmarks/reader-startup.mjs LABEL`
on each revision. The deterministic anonymous API fixture serves identical feed, directory and
profile content, with operational telemetry enabled and a fixed unsampled decision for comparable runs. UUID
generation remains unchanged. The driver records browser coverage and
screenshots on four routes, plus three fresh Lighthouse mobile/desktop runs for the feed,
topics and profile. Reports remain in ignored `reports/reader-startup/LABEL`; they measure
controlled local behavior, not production field performance. Framework and interaction code
must be retained even when initial coverage marks it unused.

## Reader stylesheet boundaries

Keep theme tokens, the reader shell and its responsive header, feed cards, account placeholders,
loading states, and card actions in the root stylesheets. The historical catalog, onboarding,
article, and settings files contained shared rules too; those rules must remain available on a
fresh feed before any route navigation or hydration.

Other styles load through the component that owns the surface:

| Surface                               | Stylesheet owner                                                  |
| ------------------------------------- | ----------------------------------------------------------------- |
| Topic/source catalogs                 | `CatalogContent` and `CatalogCard`                                |
| Settings and source suggestions       | `UserSettingsLayout` and `SourceSuggestion`                       |
| Profile editing and dev card previews | `ProfileSettings`, `DevCardPreview`, and shared dev card controls |
| Optional dev card invitation          | `DevCardPromoContent`, loaded lazily after hydration              |
| Public profiles                       | `PublicUserProfile`                                               |
| Article previews and sharing          | `ArticleModal`, `ArticlePreviewContent`, and `ArticleShare`       |
| Agent setup/consent                   | `McpContent` and `McpConsent`                                     |
| Search and legal documents            | Search components and `LegalPage`                                 |
| Reading streaks                       | The existing lazy account reading tools/menu                      |
| Topic/source preferences              | Their settings components                                         |

Use static CSS imports at these boundaries so Next.js can include styles in server HTML and wait
for them during client navigation. Keep dependency imports ordered: settings foundations before
profile overrides, and shared dev card controls before preview overrides. Next.js retains visited
stylesheets, so route styles must not change the shell or feed after navigation. Chrome and Edge
render these surfaces in one bundled document; `apps/extensions/src/newtab.css` explicitly includes
the complete stylesheet set.

The optional dev card invitation renders no server content. Its lazy boundary keeps the hidden
dialog's stylesheet out of the initial feed response, while Suspense waits for that stylesheet
before mounting the existing dialog focus, prompt-coordination, and timing effects.

Run paired lab measurements on the baseline and changed production builds with the same benchmark
script and fixtures:

```sh
node scripts/ci/browser-performance.mjs --route-styles=before
node scripts/ci/browser-performance.mjs --route-styles=after
```

Each command builds the reader, collects CSS coverage and screenshots on six reader routes, and
runs Lighthouse 12.6.1 three times per route/profile on `/latest`, `/topics`, and `/leaderboard`.
Mobile uses 412×823 at DPR 1.75 with default simulated mobile throttling; desktop uses 1350×940 at
DPR 1 with the default desktop profile. Reports, raw audits, and screenshots stay under ignored
`reports/route-styles/{before,after}`. `--skip-build` reuses an already matching production build.
Coverage describes exercised states and does not justify deleting rules for overlays or other
interactions. Browser regressions delay route CSS, check rendered frames and feed styles after
navigation, and run against the web app and both built extensions.

Paired local results on 2026-10-16 (baseline `f3355b45`, Chrome 153.0.0.0; timings are medians of
three fresh anonymous runs per profile):

| Profile / route        | CSS transferred, bytes | Unused decoded CSS, bytes | Blocking estimate, ms |       FCP, ms |
| ---------------------- | ---------------------: | ------------------------: | --------------------: | ------------: |
| mobile `/latest`       |        29,358 → 15,840 |          137,568 → 64,510 |             301 → 149 |   1,064 → 912 |
| mobile `/topics`       |        25,989 → 13,980 |          135,282 → 65,395 |             304 → 304 | 1,069 → 1,070 |
| mobile `/leaderboard`  |        27,824 → 14,735 |          139,323 → 68,261 |             152 → 151 |     920 → 916 |
| desktop `/latest`      |        29,358 → 15,840 |          139,941 → 66,761 |                39 → 0 |     303 → 256 |
| desktop `/topics`      |        25,989 → 13,980 |          137,641 → 67,554 |               53 → 56 |     301 → 300 |
| desktop `/leaderboard` |        27,824 → 14,735 |          140,808 → 69,678 |               24 → 23 |     252 → 252 |

CSS transfer includes the dialog stylesheet loaded after hydration. On the mobile feed, Lighthouse
identifies one blocking stylesheet instead of three. Unused bytes come from CDP coverage after
network idle, including lazy styles. Lighthouse's thresholded unused-CSS audit reports zero on
`/latest`, while CDP still records unused rules; zero does not mean every rule is exercised.
Catalog and leaderboard timing estimates remain essentially flat. These are local lab results,
not production field measurements. The stylesheet move retains all 4,079 existing declarations.

## Initial reader DOM measurements

Repeated feed and directory cards share compact markup across the web reader and both extensions.
Native article links, `h2` headings, dates, engagement labels/live regions and follow controls
remain available. The initial 24 articles and 60 directory entries, subsequent pages, and
history/scroll behavior are retained. Single-path Plus/Eye icons preserve the Lucide artwork;
unit tests compare their raster output with the installed originals.

Use the same benchmark scripts and lockfile on the baseline and candidate revisions:

```sh
node scripts/ci/browser-performance.mjs --reader-dom=before
node scripts/ci/browser-performance.mjs --reader-dom=after
node scripts/ci/compare-reader-dom.mjs
```

Run the first command against the baseline reader and the second against the candidate reader,
retaining both output directories in the comparison checkout. Each command builds that revision;
`--skip-build` reuses an already verified production build. The comparison fails if any card text
or native link changes, any cards disappear, or DOM/layout snapshot counts fail to decrease.
JSON samples, screenshots and the generated comparison stay in ignored `reports/reader-dom/`.

The controlled fixture uses identical content, five fresh contexts per route/profile, mobile
412×823 at DPR 1.75 with 4× CPU slowdown, and desktop 1350×940 at DPR 1. It records HTML/SVG
elements, current layout snapshot nodes (including text), and actual style/layout durations for
six alternating resize/theme cycles. It uses local network delivery rather than Lighthouse's
simulated network; timings are diagnostic and do not establish a production LCP improvement.

Against `f3355b4`, initial feed elements decreased from 1,265 to 1,145 (24 cards); topics from
951 to 831 and sources from 956 to 836 (60 cards each), in both profiles. Mobile layout snapshot
nodes decreased from 1,408 to 1,288, 1,019 to 899, and 1,024 to 904 respectively. Style/layout
millisecond medians were mixed, so this comparison establishes smaller trees without a general
rendering-speed claim.

`node --test apps/web/tests/browser/feed.mjs` and the built Chrome/Edge reader browser suites
check per-card element budgets, all 130 loaded directory headings, native links, keyboard focus,
long-name wrapping, feed pagination, accessibility and back/forward scroll restoration.

## Public document latency

The anonymous homepage uses the same streamed public shell as `/latest`; its results still
come from fresh upstream reads. Session verification finishes before choosing the guest or
personal shell, and metadata/canonicals remain in the initial head. HTML stays private with
a fresh CSP nonce per response.

Feed metadata and page content share identical resolved public feed URLs through React's
request-scoped `cache`. Language/content preferences are resolved before forming that key.
The snapshot ends with the render: another request must check the public API again, including
after visibility/publication changes. Calls with caller abort signals keep independent
transports. The API's generation-aware cache and its invalidation policy are unchanged;
Next/browser caches do not retain feed responses.

For a comparison, use the same benchmark scripts on both revisions, install the locked
dependencies, and build each revision with `npm run web:build` before measuring it:

```sh
node scripts/ci/document-latency.mjs before
node scripts/ci/document-latency.mjs before --lighthouse-only
# Run against the candidate build, retaining both report directories:
node scripts/ci/document-latency.mjs after
node scripts/ci/document-latency.mjs after --lighthouse-only
node scripts/ci/compare-document-latency.mjs
```

Reports stay in ignored `reports/document-latency/`. The driver runs a production Next server
against isolated deterministic HTTP fixtures (24 feed articles, 60 directory entries) and a
local OTLP collector. Real Next spans separate metadata, rendering and upstream timings;
request logs show session/preference calls and duplicate feed reads. Each route/identity has
a fresh process, a first-route request after the readiness probe, then seven warm requests.
A fixed 240/80/140/60/120/90/180/100 ms upstream sequence makes response-blocking variability
repeatable. These labels describe the frontend route/process, not cold PostgreSQL/Redis or
production field latency. The optional Lighthouse pass records two fresh-browser mobile and
desktop server-response-time audits on `/`, `/latest`, and `/sources` with an 80 ms upstream.

Against `860716a2`, warm guest-homepage median TTFB decreased from 142.8 to 22.6 ms and its
range width from 116.3 to 20.9 ms. Expired-session homepage median TTFB decreased from 357.5
to 129.0 ms while keeping identity verification. Topic documents dropped from four upstream
requests to three by sharing the metadata/content feed response; their latency did not improve
in this sample. Full response timings remain in the comparison to distinguish earlier shell
delivery from earlier article delivery. Deployment/network latency needs a separate live check.

Reader browser regressions verify the shell with feed responses held pending on web and both
built extensions, as well as distinct reader preferences, per-response nonces, and subsequent
unpublication/republication. These checks avoid relying on machine-dependent timing limits.
