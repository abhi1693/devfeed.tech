# Reader history restoration

Back and Forward use the reader's existing client navigation when possible. Scroll
coordinates are saved on each browser history entry alongside the router's own
state. The shared web/extension handler waits for asynchronous content to reach
that position, retries for up to three seconds, and yields immediately to input or
another navigation. It stores two numbers, never article, profile, credential, or
personal feed payloads. Filters remain in the URL; previews retain their reader
background and use the same history stack.

A new document with navigation type `back_forward` also restores those coordinates.
This covers a return after the browser discards a page rather than freezing it.

## Privacy and freshness

Every `popstate` and persisted `pageshow` checks the current session, bypassing the
usual one-minute focus throttle. Personal content, account controls, inbox, daily selections, and streak summaries are hidden before the check and
before `pagehide`. A hidden, inert subtree preserves same-account state and feed effects;
placeholder height and delayed scroll restoration prevent a jump to the top.
Logout, a different account, or a failed session check clears the account boundary
and its query cache. Engagement reloads for the confirmed session without
remounting article rows or their preview background. Focus returns to the retained
preview trigger after authorization; new reader input cancels a deferred focus
return. Existing owner-keyed feed
preferences remain mounted during a check and cannot carry over to another owner.

Public profile HTML still comes from the unauthenticated, visibility-filtered
server endpoint. Before showing a mounted/restored profile, and when it becomes
visible again, the shared client checks `/api/v1/users/{username}` with no-store and
omitted credentials. Old details and profile title/description metadata are cleared during validation; errors and revoked
visibility show an unavailable state. In-flight pre-freeze requests are aborted.
Both extension transports also omit cookies and request no-store for this endpoint.
This adds a visibility request on profile mount rather than trusting cached router
props.

## BFCache assessment

The 2026-10-07 production Lighthouse finding in [issue #132](https://github.com/abhi1693/devfeed.tech/issues/132)
reported `CacheControlNoStoreCookieModified`, `MainResourceHasCacheControlNoStore`,
and `JsNetworkRequestReceivedCacheControlNoStoreResource`.

The local anonymous feed reproduction disables optional analytics configuration,
uses no synthetic cookie writes, and removes Playwright's default
`--disable-back-forward-cache` launch argument. Even without analytics cookie
changes it reports `pageshow.persisted === false`, with
`response-cache-control-no-store` and
`response-cache-control-no-store-with-js-network-request` in the browser's
`notRestoredReasons`. The dynamic nonce-based document and session/engagement
requests retain their existing no-store contracts. An anonymous-looking feed can
still belong to a signed-in browser, so it cannot bypass session discovery or
reuse personalized engagement responses as public data.

Authenticated/account pages and public profiles are assessed separately from the
public feed: they require fresh session or visibility authorization and retain
private/no-store headers. No header has been relaxed to improve a Lighthouse
score. `/auth/me` does not mint an anonymous session cookie; anonymous article
opens can create the visitor cookie used for engagement deduplication. Optional
analytics cookie writes may be additional production blockers, but removing them
alone cannot resolve the blockers reproduced without analytics.

[Chrome's no-store BFCache guidance](https://developer.chrome.com/docs/web-platform/bfcache-ccns)
explains why cookie changes and no-store JavaScript responses can disqualify a
no-store document. Browser eligibility can change; neither no-store alone nor a
future cache hit replaces the session/visibility guards. Persisted pageshow
handling has regression coverage; these measurements prove the safe fallback,
not a BFCache hit.

## Reproduce and compare

Build the website and both extensions, then run these sequentially on an idle host:

```sh
npm run web:build
npm run extension:build:all
node tests/benchmarks/reader-history.mjs
DEVFEED_HISTORY_PLATFORM=chrome node tests/benchmarks/reader-history.mjs
DEVFEED_HISTORY_PLATFORM=edge node tests/benchmarks/reader-history.mjs
```

The harness serves deterministic disposable fixtures and never mutates production.
It checks real web, unpacked Chrome, and unpacked Microsoft Edge readers, including
scroll on public and personal routes, the Oldest filter, Forward, preview
Back/Forward, fresh engagement, expired sessions, and revoked profiles. Web tests
also capture real document navigation type, persisted pageshow, and BFCache failure
reasons. The same assertions run in `ci:reader-parity`.

For a baseline checkout, build that checkout and use `--baseline` (records known
failures instead of accepting them). `DEVFEED_HISTORY_WEB_ROOT` and
`DEVFEED_HISTORY_EXTENSION_ROOT` can point at its built outputs. Reports and local
screenshots are ignored under `reports/reader-history/{before,after}/{platform}`;
CI uploads the after reports with its reader-parity artifact.

Timing samples are local lab observations, not field p75 or production Lighthouse
scores. Public-feed readiness means the first article is visible; scroll-ready
time additionally waits for the saved position. A failed baseline restoration is
reported as a failure after a four-second observation window, not a four-second
page-load measurement. Each platform uses three public-feed returns, with an
1100×800 viewport and reduced motion, without simulated CPU/network throttling.

## Measured comparison — 2026-10-10

Baseline is `master` at `4c252771`; after is `fix/reader-history-restoration`.
Both used the same fixture harness, browser versions, viewport, and host settings.
These are medians of three returns per platform:

| Platform             | First article visible, before → after | Public feed scroll, before → after | After scroll-ready time |
| -------------------- | ------------------------------------- | ---------------------------------- | ----------------------- |
| Web 153.0.8010.12    | 38 → 43 ms                            | 650px → 650px                      | 58 ms                   |
| Chrome 153.0.8010.12 | 57 → 69 ms                            | failed (0px) → 650px               | 81 ms                   |
| Edge 141.0.3537.92   | 92 → 108 ms                           | failed (0px) → 650px               | 130 ms                  |

Personal Read later scroll failed in all three baseline runtimes and restores to
650px in all three after runtimes. Expired personal articles remained visible in
all baselines and are hidden after the change. A revoked profile remained visible
on web Back in the baseline; all after runtimes hide it, and web title/description
metadata is also cleared. Extension baseline profile navigation already refetched
on route mount; it now additionally checks visibility on a retained document's
return. Session rechecks and fresh engagement assertions pass in all after runtimes.
Filters, Forward, and preview history pass before and after.

Full-document web Back remains a non-BFCache navigation: the median article/scroll
ready time was 188ms before and 178ms after, with 650px restored in all samples.
These small local differences do not establish a full-document performance improvement. The safe
client-navigation fallback is the fast path shown above. The document comparison
reports the same no-store blockers, no optional analytics scripts, and zero
cookies in both revisions.
