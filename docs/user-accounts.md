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

Opening a preview or article records an open; prefetching and viewing a card do
not. Opens are deduplicated per article, user or anonymous browser, and UTC hour.
Anonymous browsers receive an opaque HttpOnly visitor cookie; no IP addresses or
fingerprints are stored. Signed-in and anonymous identities are separate. The
scheduler removes deduplication records older than 30 days in bounded batches,
while lifetime counters remain. Public responses expose aggregate counts only.

Trending ranks the last seven days of opens and likes, restricted to eligible
public articles. It is currently hidden from both navigation layouts until there
is enough engagement data, and its page is excluded from indexing. Tracking
continues while discovery is hidden; there is no automatic launch threshold.

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
