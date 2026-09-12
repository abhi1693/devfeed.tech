# Optional user accounts

Everyone can browse, search, and open topic/source/article pages without signing
in. Users who want a personalized feed can sign in or create an account, choose
topics at `/preferences`, and read `/my-feed`. Preferences persist across sessions
and devices. My feed reads precomputed recommendations from follows, likes and one
approved topic-relationship hop. Ranking combines interest strength, freshness and
topic variety. Published/reviewed articles, approved sources and current topic
assignments are checked again when serving. See [recommendations](recommendations.md)
for refresh behavior, stored relationships and operating limits.

The `apps/user-api` service owns `/v1/user/*` on internal port 8002. The public
Next website forwards same-origin `/api/v1/user/*` requests to it. Public discovery
still uses the anonymous API; the web service has no startup dependency on user
sign-in availability. User API configuration, Redis session keys and browser
cookies are separate from admin. User identities require organization membership,
not an admin role, and a user session cannot authorize admin API requests.

## ZITADEL configuration

Create a separate application in the existing organization. Use authorization code
with PKCE S256 and the same issuer and organization as admin. For a public PKCE
client use token endpoint authentication `none`; for a confidential client set its
own user secret and matching authentication method. Never reuse a confidential
client secret belonging to a different application.

Register the exact redirect URI:

```
<DEVFEED_USER_BASE_URL>/api/v1/user/auth/callback
```

For local development at `http://192.168.1.101:3000`, this is
`http://192.168.1.101:3000/api/v1/user/auth/callback`. Enable development HTTP
redirects in the ZITADEL application when using HTTP. Production must use HTTPS
and `DEVFEED_USER_COOKIE_SECURE=true`. Make sure the organization's login policy
allows self-registration if users should be able to create accounts. The
[ZITADEL authorization endpoint](https://zitadel.com/docs/apis/openidoauth/endpoints)
supports `prompt=create` for the explicit Create account action.

Set the independent `DEVFEED_USER_*` values shown in `compose.env.example`.
There is no implicit fallback to admin credentials. Compose passes user OIDC
credentials only to `user-api`; the Next server receives only API/base origins.
Changing the issuer, client, organization, origin or session policy invalidates
existing sessions. Browsers receive only HttpOnly session cookies and a CSRF token;
provider tokens and client secrets stay on the server. Sessions expire at the
earlier of the ID-token expiry and the configured session lifetime. Sign-out
revokes the local user session; it does not sign out other ZITADEL applications.

## Profile settings

The signed-in navbar menu provides Profile settings, Your topics and Sign out.
`/settings/profile` lets users save a display name and public photo URL; the form follows the admin settings layout, with horizontal fields, an inline
photo preview, and Reset to defaults / Save changes controls. Reset clears local
overrides and requires saving to persist. A successful save updates the menu immediately and persists across
sign-ins. Sign-in name and email remain read-only and separate from these overrides.

`GET` and `PUT /v1/user/settings/profile` use the authenticated account identity;
writes also require CSRF. Migration `0001` adds the profile JSON field.
Empty overrides use the provider name and initials, and failed photo loads fall
back to initials. Unsafe photo URLs are rejected by the shared profile validator.

## Article previews and engagement

Cards open a routed preview modal. Direct article URLs, reloads and sign-in
returns open that same modal over the latest feed; no standalone article view
is retained. Closing a direct-link modal returns to the feed. URLs and article
metadata remain available for sharing and indexing. The wide preview puts publisher details, title and summary before its uncropped
supporting image. A side panel shows the featured topic icon, brief and Follow
button; it stacks below the content on mobile. An expandable source excerpt
retains the publisher description. Its footer keeps the heart and original article
button accessible while the content scrolls.

Signing in and `/register` go directly to the hosted ZITADEL interface. Anonymous
heart clicks start sign-in and return to that article; liking requires an
authenticated user and CSRF token. Each user can like an article once and remove
their own like. Topic following uses `PUT /v1/user/preferences/topics/{topic_id}`
with `{ "followed": true }` (or `false` to unfollow), session ownership and CSRF checks. This atomic
update preserves other followed topics and enforces the existing 100-topic limit;
anonymous Follow links return from hosted sign-in to the article modal. My feed navigation appears only after sign-in.

Clicking "Read article" records an outbound open; opening previews, prefetching,
and viewing cards do not. This measures intent to read, not completed reading. Opens are deduplicated per article, user or anonymous browser, and UTC hour.
Anonymous browsers receive an opaque HttpOnly visitor cookie; no IP addresses or
fingerprints are stored. Signed-in and anonymous identities are separate. The
scheduler removes deduplication records older than 30 days in bounded batches,
while lifetime counters remain. Public responses expose aggregate counts only.

Missing, malformed, expired or revoked session cookies are treated as anonymous
for engagement reads and original-article clicks. They still use visitor
deduplication and every shared anonymous abuse budget. This fallback does not
authorize likes or account changes, suppress CSRF failures, or bypass a session
store outage. Counting is independent of GA4 and does not wait for sign-in to load.

Tracking uses atomic Redis request budgets: 30/minute and 180/hour per viewer;
anonymous traffic also shares 20/minute and 100/hour per article, plus 120/minute
and 1,000/hour across the app. Cookie rotation cannot bypass the shared caps.
Limits apply across service replicas and return 429 with Retry-After; unavailable
Redis returns 503 before article queries/writes. Tracking failure never blocks the
original link. These ceilings bound abuse, not prove a viewer is human; a popular
article can reach the anonymous cap and stop counting until it expires.

Trending ranks the last seven days of opens and likes, restricted to eligible
public articles. It is currently hidden from both navigation layouts until there
is enough engagement data, and its page is excluded from indexing. Tracking
continues while discovery is hidden; there is no automatic launch threshold.

## Reader list pagination

The shared `apps/web/src/components/infinite-scroll.tsx` component provides the
scroll boundary, loading status, manual fallback, retry and end states.
`use-infinite-pages.ts` handles serial requests, cancellation and repeated cursors.
Feeds (including filtered, topic, source, personal and Trending views) and the
topic/source directories use these components. Requests pause on tab blur or
visibility loss and resume at the same position when the page becomes active.

Directory pages fetch 60 records at a time through fixed public API routes.
Source directories and source preference choices request `has_articles=true`,
excluding sources without published, publicly visible articles before pagination.
Topic discovery uses the equivalent filter.
Overlapping results are deduplicated, and failures retain already loaded cards.
Preference selectors reveal 60 choices at a time using the same scroll component;
their complete catalog remains loaded for search and saved selections.
Trending returns a bounded continuation cursor and a final page with no cursor.
Its live rankings can shift as engagement changes; repeated articles are hidden
when appending pages.

To reuse scrolling, provide `InfiniteScroll` with children, loading/error state,
`hasMore` and `onLoadMore`. Supply `nextHref` when a navigable fallback exists.
Use `useInfinitePages` for remote pagination and key the consuming component by
its filters so route changes discard stale requests and items.

## Local development and storage

Apply migrations through the normal migration step before starting the new
service. The `0001` baseline creates the account and engagement tables, including
`user_accounts` and `user_topics`. Accounts are keyed by
issuer plus subject, never matched by email. Preferences are scoped to the signed
session and written atomically, with a maximum of 100 followed topics.

For host development, configure the root `.env`, then run:

```sh
uv run alembic upgrade head
uv run uvicorn devfeed_user_api.main:app --host 127.0.0.1 --port 8002 --no-access-log
```

Run the website with `DEVFEED_PUBLIC_API_URL`, `DEVFEED_USER_API_URL`, and
`DEVFEED_USER_BASE_URL` set in its server environment. `next dev` does not load
root `.env` automatically when run from `apps/web`; export these three nonsecret
values or put them in ignored `apps/web/.env.local`. All account responses are
uncached; account pages and callback routes are excluded from indexing. Ordinary
public entity routes remain indexable.

Tests cover signed OIDC exchanges, browser state and nonce binding, registration,
organization validation, cookie/service isolation, CSRF, revocation, database
ownership, atomic preferences, publication eligibility and constant feed query
counts. No external identity provider is contacted by the test suite.

`GET /v1/user/auth/me` is an optional session probe: it returns the current user
or JSON `null` with HTTP 200 when signed out or the session has expired. Session
store/configuration outages still return 503. Protected feeds, preferences and
likes continue to require authentication; this probe does not grant access.

Signed-in users also have a Chimely notification inbox for newly published articles
matching topics they already follow. Notifications open the article preview modal.
Delivery is deduplicated per article/user and runs through the durable background
outbox. See [notifications](notifications.md) for matching, retry and isolation rules.

## User app analytics

The user web app supports Google Analytics with measurement ID `G-N4V5CW5C0M` in
production mode. Set `DEVFEED_ANALYTICS_ENABLED=false` to disable the tag, page
views and custom events. Docker Compose and the example environment files default
this flag to `false`, even though the container runs with `NODE_ENV=production`.
Set it to `true` to explicitly enable analytics in a production-mode process.
Development/test mode always disables analytics. An unset flag retains the existing
production default; blank, false or unrecognized values disable analytics.

This is a server-side runtime setting, not a `NEXT_PUBLIC_*` build-time variable.
The root layout uses Next.js dynamic rendering so even otherwise static pages read
the flag when the container serves the request. The same built image can therefore
be used locally and in production. After changing a Compose environment value,
recreate the web container; future flag changes do not require rebuilding the image.
For a local standalone run, use `DEVFEED_ANALYTICS_ENABLED=false npm run web:start`.
See [Next.js runtime environment variables](https://nextjs.org/docs/app/guides/environment-variables#runtime-environment-variables).

Like the Wardn AI website, the enabled tag defers loading until the
first pointer, keyboard, scroll or touch interaction, or page exit. Initialization
is deduplicated and respects `window['ga-disable-G-N4V5CW5C0M']`. The admin app
does not include the tag. `GOOGLE_ANALYTICS_ID` can override the ID at runtime.

Custom events use GA4's `gtag('event', ...)` API and explicitly route to this
measurement ID with `send_to`. The first action initializes the deferred tag and
queues configuration before the event, so it is not lost while gtag.js loads.

| Event | Trigger | Custom parameters |
| --- | --- | --- |
| `article_open` | Click or middle-click on Read article, regardless of backend tracking availability | `article_id` |
| `article_like`, `article_unlike` | Successful like/unlike | `article_id` |
| `topic_follow`, `topic_unfollow` | Successful individual follow/unfollow | `topic_id` |
| `source_follow`, `source_unfollow` | Successful individual follow/unfollow | `source_id` |
| `topics_saved`, `sources_saved` | Successful bulk preference save | `selected_count` |
| `source_suggested` | Successful suggestion submission, excluding name lookups | `source_type` |
| `feed_settings_saved` | Successful feed preference save | `feed_view`, `selected_count` |
| `appearance_settings_saved` | Successful appearance preference save | `theme` |

These payloads omit account IDs, names, emails, free text, source submission URLs,
and authentication tokens. Page impressions, profile reads, preview opening, and
failed writes do not emit these custom events. GA `article_open` measures click
actions; DevFeed's own open counter additionally deduplicates and rate-limits them.

Events can be inspected in GA4 Realtime/DebugView after deployment. To use the
custom parameters as reporting breakdowns, register event-scoped custom dimensions
for the relevant string parameters and a custom metric for `selected_count` in
GA4 Admin → Custom definitions. Register only dimensions needed for reporting;
article/topic/source IDs have high cardinality. This repository sends events but
does not change the Google Analytics property's reporting configuration.
See [Google's event parameter guide](https://developers.google.com/analytics/devguides/collection/ga4/event-parameters).
