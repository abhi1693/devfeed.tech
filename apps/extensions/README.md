# DevFeed

A Chrome and Microsoft Edge Manifest V3 extension that renders DevFeed directly inside each new tab,
without redirecting to the website or embedding it in an iframe.

The extension bundles the web reader's React components and styles. Feed, search,
article previews, personalized feed, read later, trending, topics, sources, source suggestions,
and all six settings sections use local hash routes. Publisher links and website
sign-in open a separate tab. Chrome's own new-tab footer is browser UI and is not
part of the extension's layout.

Transport eligibility and request policies come from generated operation definitions.
Run `npm run web:generate` after user API or public reader binding changes. Private
operations reuse the reader gateway identities; public adapters have explicit bindings
in `apps/web/src/lib/api/reader-bindings.json`. Unknown operations, unsupported methods
and direct login/callback requests are rejected before reading bodies or sending requests.
Each eligible operation declares browser credentials, request cache headers, forwarded
headers and a total timeout. Avatar operations retain a 45-second timeout; other reader
operations use 15 seconds. Collecting an original `Request` body also has a byte limit,
a reading deadline and caller cancellation. The browser supplies HttpOnly cookies.

Article previews retain the current feed, saved articles, or search results underneath
them. Opening, navigating, and closing a preview keeps the loaded list in place without
reloading the background page.

Navigation carries typed page or article destinations with explicit scroll policy.
Article overlays receive the requested slug and entry/dismissal state independently of
loaded content. Extension history records the validated background destination;
opening an article from a notification retains the contextual page and reading position.
Notification settings links use the reader's native web or local hash destination and
reset scroll when moving to settings.

Web collection pages and both extensions share route validation in
`apps/web/src/lib/reader-routes.ts`. An absent content suffix selects All; an unknown
suffix or route shows the shared missing page before catalog or feed requests.
Extension dispatch consumes the resolved route, including article-preview backgrounds.
Invalid saved backgrounds show the missing page; direct article entries use Latest.
Unsupported website pages remain external links.

Each reader route declares its feed mode and placement capabilities. Home uses Personal
for signed-in readers and Latest for guests; typed and catalog routes use contextual
placements. `apps/web/src/lib/reader-feed.ts` shares filter normalization and placement
eligibility with web pages, including sponsored fallback, native formats, and source/cursor
exclusions. Changing a route URL does not change these behaviors.

Rendered reader pages and active previews publish prompt eligibility through the shared
reader context. Signup progress counts loaded articles, including cached previews;
loading or unavailable articles add no visit. Foreground previews suppress automatic
invitations and Must Reads until reading ends. Settings, consent, authentication,
and missing pages stay quiet; manual Must Reads remains available while reading.
Session checks pause presentation and actions while preserving the current account's
pending daily claim. Logout and account changes clear that state.

## Build and install

From the repository root, with Node.js 22.22.2+ on 22.x, 24.15.0+ on 24.x, or 26+:

```sh
npm ci
npm run extension:build
```

1. Open `chrome://extensions` in desktop Chrome and enable **Developer mode**.
2. Choose **Load unpacked** and select `apps/extensions/dist/chrome`.
3. Open a new tab and accept Chrome's change prompt if displayed.

After rebuilding, click **Reload** on the extension and open a new tab. Refreshing
a tab alone does not reload the manifest or its content security policy. Disable
other new-tab extensions if they conflict. Removing DevFeed restores the previous
new-tab behavior. Chrome does not override incognito new tabs.

The bundled public manifest key gives unpacked installs the stable ID
`hliakjocndflpkmfajndigbpngfcekdm`. If an earlier unpacked install used a different
ID, remove that install and load this directory again. No private signing key is
included. For Web Store distribution, use the store-assigned public key and ID,
and update the server allowlist to match.

## Microsoft Edge

The Edge target uses the same Manifest V3 source, reader, account features, styling,
and artwork as Chrome. `chrome/manifest.json` is the shared version and manifest
source; Edge uses the supported Chromium manifest keys, including
`chrome_url_overrides`. There is no separate copy of the application to maintain.

```sh
npm run extension:edge:build
npm run extension:edge:test:browser
npm run extension:edge:package
```

Open `edge://extensions`, enable **Developer mode**, choose **Load unpacked**, and
select `apps/extensions/dist/edge`. Enable the new-tab replacement if Edge prompts,
then open a new tab. Reload the extension after rebuilding.

Upload `apps/extensions/dist/devfeed-edge-extension-0.1.13.zip` to Microsoft Partner
Center for Edge Add-ons. Its root manifest omits the development `key` and any
Chrome `update_url`. Chrome's existing build and ZIP commands remain available.
Both unpacked builds retain the same development ID. The Edge store assigns its
own ID: add it alongside the Chrome store ID in `DEVFEED_USER_EXTENSION_IDS` on
both the web gateway and user API before testing the published account actions
and analytics. Do not replace the Chrome ID or broaden the origin allowlist.
Store publication and server configuration are separate from building this ZIP.

The Edge browser command runs the same guest and authenticated regression suites
using an installed Microsoft Edge (`msedge` Playwright channel). An explicit
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` overrides the executable. Test traffic stays
on local fixtures; it does not verify production deployment or a store-assigned ID.

See Microsoft's [Chrome porting guide](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/port-chrome-extension)
and [publication guide](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension).
Use the existing DevFeed artwork and accurately disclose the same data handling
in the Edge listing.

## Sign-in and server configuration

Sign in through the existing website login flow, then return to the new tab. Chrome
supplies the website's HttpOnly session cookie using the DevFeed host permission.
No additional OIDC client or extension callback is required. Session checks run
when a tab becomes active; checking the same session preserves its feed and modal.
Public search results and loaded pages survive credential refreshes within the
same account. Search ads revalidate using the refreshed credentials and loaded context.
Sign-out clears personal UI and notifies other open extension tabs.
Session expiry is checked using bounded timers, so 30-day website sessions do not
overflow the browser timer limit and immediately clear the signed-in account.

Account writes require the updated web gateway and user API from this change.
Set this value on **both** services (Compose forwards it to both):

```sh
DEVFEED_USER_EXTENSION_IDS='["hliakjocndflpkmfajndigbpngfcekdm"]'
```

Only exact configured extension origins are trusted. Existing session validation
and CSRF checks remain required for likes, bookmarks, follows, settings, notification
actions, source suggestions, and sign-out. The default empty allowlist grants no
extension write access. Local extension builds do not deploy these backend changes.

The new public `/api/v1/articles/[slug]` route supports article links reached from
search, notifications, and direct navigation. Until deployed, previously selected
feed articles still open from the public tab cache. A bounded `sessionStorage`
cache of public article/topic records survives same-tab reloads; it contains no
sessions, CSRF tokens, recommendation reasons, or personal engagement state.
Snapshots expire after 24 hours. Storage restrictions can prevent reload recovery.

The `/api/v1/feed/options` route provides contextual filter choices and is required
for extension feed filters. If options are unavailable, successfully loaded articles
remain readable, and refreshing the tab restores filters once the route recovers.
Topics and sources use the existing paginated public catalog endpoints.

## Reader parity

Grid/List icons beside Filters share the website's article layout control. Grid is
the default; List uses compact rows across My feed, Latest, topic/source feeds,
Trending and Read later. The last two expose the control in their headings. Guest
choices stay in the extension's browser storage; signed-in choices use account feed
settings without changing content or language selections. Loaded pages, bookmarks,
engagement data and preview navigation survive layout changes. `/trending` is a
local reader route in both extensions.

My feed uses `/`, the public latest feed uses `/latest`, and signed-out new tabs
redirect to `/latest` without Read later navigation. Both extensions use the web
reader's recommendation component and retain its current generation during likes,
background rebuilds, session checks and explicit refreshes.
The personal feed starts loading on mount, including when focus stays in the
address bar or the tab is in the background. Pending recommendation refreshes
continue without a focus event; leaving the feed cancels its requests and timers.
Analytics engagement still requires a visible, focused page.

Every reader change must pass the web, Chrome and Edge browser journeys:

```sh
npm run reader:test:parity
```

CI runs this gate on Linux x86_64 with Chromium and Microsoft Edge installed.
Pre-commit also checks extension types, unit tests and both builds whenever reader
or shared UI code changes. Store uploads remain separate from website deployment.

Browser scenarios are separate named tests with isolated fixtures. The default run
includes every scenario; use Node's test-name filter for a focused run after fresh
web and extension builds:

```sh
node --test --test-name-pattern="reader routes" apps/web/tests/browser/feed.mjs
node --test --test-name-pattern="reader routes" apps/extensions/tests/reader.browser.mjs
DEVFEED_EXTENSION_BROWSER=edge node --test --test-name-pattern="reader routes" apps/extensions/tests/reader.browser.mjs
```

These checks cover missing pages, recovery links, every content suffix, catalog scope,
and retained article previews. Screenshots stay under ignored `reports/reader-routes/`.

Use `--test-name-pattern="feed modes"` with the same browser runners for focused guest
and signed-in feed-mode checks, placement request policies, and preview retention.

Use `--test-name-pattern="reader context"` to check loaded-article visit counting, prompt
suppression, manual Must Reads, and deferred presentation claims in all three runtimes.

Use `--test-name-pattern="reader navigation"` for typed navigation and overlay checks:
scrolled feeds, sequential previews, Back/Forward replay, direct and failed article
entries, inbox article actions, and native settings links. Screenshots stay under
ignored `reports/reader-destinations/`.

Use `--test-name-pattern="login destinations"` for shared sign-in destinations: approved
reader filters, topic/source follows, article bookmarks, signup invitations, and
scoped MCP authorization. Provider redirects run against isolated fixtures; Chrome
and Edge retain the reader in its original tab and open sign-in separately.
Screenshots stay under ignored `reports/login-destinations/`.

The remaining reader scenarios use `server reads`, `user mutations`, `reader theme`,
`reader sidebar`, and `reader journey`. Authenticated extension scenarios live in
`tests/auth.browser.mjs`: filter by `authenticated navigation`, `reading streak`,
`must reads`, or `authentication journey`. `DEVFEED_EXTENSION_BROWSER=edge` selects
the actual browser and extension package; scenario selection does not use application
environment variables or server responses.

## Validate and package

```sh
npm run extension:check
npm run extension:test
npx playwright install chromium
npm run extension:test:browser
npm run extension:package
```

Browser tests load the real unpacked extension. Guest tests use fixture API
responses; authenticated tests run a disposable local HTTPS server and require
OpenSSL. The browser maps `devfeed.tech` to that server only within the test profile,
so sign-in, cookies, CSRF writes, settings, and sign-out never touch production.
They also cover local catalogs, modal focus/reload, notification styling, search,
pagination/retry, theme persistence, and mobile layout.
Personal-feed error and stale-cursor recovery reload the local route. The keyboard
skip link focuses the main content without replacing the current hash route.
Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to use an existing Chromium installation.
Screenshots are saved under `apps/extensions/dist/reader-*.png`.

Packaging requires Python 3 and creates
`apps/extensions/dist/devfeed-chrome-extension-0.1.13.zip` with the manifest at the ZIP root.
Release ZIPs use `devfeed-<browser>-extension-<version>.zip`, where `<browser>` is
`chrome` or `edge` and `<version>` comes from the extension manifest.
Packaging removes the development `key` field from the ZIP manifest; the source
and unpacked manifests retain it. The published GitHub release workflow submits
each package to its corresponding browser store. Increment the Chrome manifest
version before a published update; the Edge package uses the same release version.
The web Docker builder includes extension sources for shared analytics type checks;
store archives are excluded from the build context and are not shipped in the web image.
When changing shared UI, also run `npm run web:test` and `npm run web:lint`.

## Release delivery

Every application tag runs an extension release version gate. If extension code or
shared reader UI changed since the prior application tag, the gate requires a higher
extension manifest version. For an immutable GitHub release, create it as a draft and
manually run the **Browser extension release** workflow with that tag before publishing.
The workflow builds both browser packages and attaches them to the draft. Publishing
then verifies those assets against the tagged source and starts store submission. A
mismatch fails the release workflow because published assets cannot be replaced safely.

After verifying the release assets, GitHub Actions submits both packages to the
Chrome Web Store and Microsoft Edge Add-ons APIs. Workflow logs record each
submission result and manifest version. Public release notes
remain focused on user-facing changes. Store certification still applies, so
submission success does not mean either store has approved or published the update.
The workflow uses the repository Actions secrets `CWS_SERVICE_ACCOUNT_JSON` and
`EDGE_ADDONS_API_KEY`, plus the variables `CWS_PUBLISHER_ID`, `CWS_EXTENSION_ID`,
`EDGE_ADDONS_CLIENT_ID`, and `EDGE_ADDONS_PRODUCT_ID`.

## Distribution and privacy

Public installation requires the respective Chrome Web Store or Microsoft Edge
Add-ons developer account, artwork, and privacy disclosures. First-time listing setup
remains in the store dashboards; subsequent package submissions are automated. Store
review and live-version confirmation remain separate from the website release.
See the [publication guide](https://developer.chrome.com/docs/webstore/publish).

The sole host permission is `https://devfeed.tech/*`. The extension requests no
history, tabs, cookies, or content-script permissions and executes no remote
JavaScript. Inline CSS is allowed because the shared notification and popover
components insert styles dynamically. Optional GA4 analytics uses the website property,
as described below. Search
queries go to DevFeed; publisher images load directly without a referrer and use
Chrome's normal image cache. Account preferences synchronize through the user API.
Website activity follows the [DevFeed privacy policy](https://devfeed.tech/legal/privacy).

Chrome documents [new-tab overrides](https://developer.chrome.com/docs/extensions/develop/ui/override-chrome-pages)
and [extension cookie behavior](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies).

## Extension analytics

The extension sends events to `/api/v1/extension/analytics` on DevFeed. The web
server relays approved events to GA4 using Measurement Protocol. The API secret
stays on the server; it is never bundled, returned by the endpoint, or sent by the
extension. Website and extension tracking use the same measurement ID.

Configure the **web service** with:

```sh
DEVFEED_EXTENSION_ANALYTICS_ENABLED=true
DEVFEED_EXTENSION_GA_API_SECRET=<server-only-secret>
DEVFEED_USER_EXTENSION_IDS='["hliakjocndflpkmfajndigbpngfcekdm"]'
```

Local examples and Compose default to disabled, independently of website GA4.
The relay fails closed if credentials are missing. Its public GET returns only whether
tracking is enabled;
POST requires an exact allowed extension origin, bounded input, and known events.
Origin checks and per-process rate limits mitigate abuse; they do not authenticate
arbitrary HTTP clients. An upstream failure is dropped without retries.

Page views use normalized paths under the reporting-only hostname
`extension.devfeed.tech`. Approved screen labels come from the same reader route
descriptors used for navigation. Public profiles report `/users/profile`, article
previews report `/articles`, and unsupported routes report `/other`. Raw searches,
query strings, article titles, account IDs,
emails, session cookies and CSRF tokens are not sent. Events include article opens,
likes, bookmarks, follows, source suggestions, and preference changes. Active,
focused reading time is reported with a shared 30-minute inactivity session and a
random installation ID stored in extension-local storage. It is separate from
website analytics cookies. Realtime/session reporting uses `session_id` and
`engagement_time_msec`; Measurement Protocol does not reproduce all browser-tag
attribution or device/location data automatically.

Do Not Track and Global Privacy Control disable extension tracking. An explicit
local override can also disable it: set
`localStorage["devfeed:extension-analytics-disabled"] = "true"` in the extension's
DevTools and reload the tab. Tracking failures or unavailable local storage must
not affect browsing. The Web Store privacy disclosures must include this analytics
collection when enabled.

The automated browser tests send only to a disposable local relay fixture. Google's
`/debug/mp/collect` endpoint can validate payloads without recording test events;
an HTTP 2xx from `/mp/collect` alone does not prove GA4 processed an event.

After fresh web, partner and extension builds, check the actual Faro views, partner
request metrics and extension page-view envelopes with:

```sh
node apps/web/tests/browser/telemetry.mjs web
node apps/web/tests/browser/telemetry.mjs partner
node --test --test-name-pattern="telemetry labels" apps/extensions/tests/reader.browser.mjs
DEVFEED_EXTENSION_BROWSER=edge node --test --test-name-pattern="telemetry labels" apps/extensions/tests/reader.browser.mjs
```

Search result type, article order and date filters apply immediately in both
extensions and the web reader. Search displays a visible loading message while
results refresh; there is no separate Apply action.

The website offers a browser-specific store button beside feed controls and My feed
settings. It opens the matching store in a new tab and is hidden in extension pages
and unsupported browsers. Browser logos are served locally by the website.

Article feeds and search results append automatically as you scroll, without a
More articles control. Loading, retry, and end-of-feed states share the web reader
component in both browser extensions.

Version 0.1.10 shares the website's recoverable article error content and retry
button. Both browser suites verify that an uncached article can recover after
an upstream 503. Packaging does not publish either browser-store listing.
