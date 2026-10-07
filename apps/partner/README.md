# DevFeed partner portal

The standalone Next.js portal is intended for `https://partner.devfeed.tech`. It has its own
Python API in `apps/partner-api`, separate from the public reader, user API, and admin API.
Run `npm run partner:dev` (port 3002) and
`uv run uvicorn devfeed_partner_api.main:app --port 8004 --no-proxy-headers --no-access-log`.
Set `DEVFEED_PARTNER_API_URL=http://localhost:8004` and
`DEVFEED_PARTNER_BASE_URL=http://localhost:3002` for both processes. The API also needs the
normal database/Redis settings and `DEVFEED_PARTNER_COOKIE_SECURE=false` for local HTTP.
Compose and its build overlay include both services; only the frontend port is exposed.

## Access model

Create a separate Zitadel OIDC application in the DevFeed project with the exact redirect URI
`https://partner.devfeed.tech/api/v1/partner/auth/callback`. Configure the same organization,
issuer, and role claim policy as administration. Grant the application the `partner` and
`superuser` project roles. The API requests both role scopes and checks authenticated role
claims against the configured organization. An ordinary reader role does not grant portal
access. The partner API reads `DEVFEED_OIDC_CLIENT_ID` / `DEVFEED_OIDC_CLIENT_SECRET`; Compose
maps `DEVFEED_PARTNER_OIDC_CLIENT_ID` / `DEVFEED_PARTNER_OIDC_CLIENT_SECRET` into those values.
Use a separate process environment when launching the API directly.

Portal access requires either `partner` or `superuser`. A partner sees active accounts linked
by database memberships keyed by the validated issuer and immutable Zitadel subject ID.
One account can have many members; one member can belong to many accounts. Role access alone
shows an empty account state. Emails and requested account IDs never grant data access.
Membership deletion and account pausing take effect on the next data request. Unavailable
accounts return 404, including foreign account IDs. Zitadel role grants are captured at login
and bounded by the session lifetime; removing a role also requires revoking existing portal
sessions in Redis for immediate removal. Session cookies and Redis keys are isolated from
reader/admin sessions. Mutations require origin and CSRF verification.

Superusers need no membership and see active and paused accounts through the same account
selector. They can create accounts, edit tier/benefits, add or remove members, associate
catalog products or ads, and change asset status. Assign roles in Zitadel; membership forms
only control which partner accounts the user can access. Partners cannot manage grants,
tiers, assets, or measurement totals. Tier names and benefits are explicitly managed per
account; this change does not invent a billing plan or automatic feature entitlements.

## Measured value

Accounts own `partner_assets` (product placements or ads). Product assets reference canonical
catalog products; catalog providers and commercial partner accounts remain independent.
Each asset belongs to one account, keeping reporting attribution stable. Shared canonical
products can have distinct assets for different partner accounts. Membership, ownership,
and metrics are never inferred from an imported listing or its email/domain.

The dashboard shows impressions, clicks, CTR, per-asset results, daily activity, a 7/30/90/365
day selector, tier, and benefits. Periods use inclusive UTC calendar dates through today.
CTR is total clicks divided by total impressions, not an average of daily percentages.
Summary totals include all assets even when the asset table is paginated. A day with no
measurement is distinct from a measured zero; coverage and latest update are visible.
Revenue, conversions, and financial ROI are not claimed by these metrics.

### Impression and click tracking infrastructure

The website and both extensions share `observePartnerPlacement(element, receipt)` in
`apps/web/src/lib/partner-tracking.ts`. Future placement rendering should attach it to the
clickable placement element and clean it up on unmount. It counts an impression after at
least half the placement stays visible for one continuous second while the document is
visible. It counts a click independently, including keyboard and middle-button activation.
Retries use the same receipt; errors never block link navigation. No placements are launched
by this infrastructure change.

Trusted delivery code issues one receipt per placement render with
`PartnerTrackingTokens(key).issue(asset_id)` from `devfeed_core.partner_tracking`. Keep the
receipt stable across retries and re-renders of that delivery. Never share or cache one
delivery receipt across different readers. The public API verifies the
server-only `DEVFEED_PARTNER_TRACKING_KEY` (at least 32 random ASCII characters). Only trusted
server delivery code may hold the key; never put it in a browser or extension. Receipts bind
an asset, an anonymous delivery UUID, and the UTC delivery time, and expire after one hour.
Issue them when delivering an active account-owned placement, not for private catalog matches.

The shared client POSTs `{ "kind": "impression" | "click", "receipt": "..." }` to
`/api/v1/partner-tracking/events`. The website forwards only that bounded JSON body to the
public API's `/v1/partner-tracking/events`. Extensions use that same website endpoint without
cookies, authorization headers, or referrer URLs. The collector rejects forged, expired,
missing, and disabled receipts, applies shared Redis event limits, and checks account/asset
status live. It stores no reader identity, IP address, or destination URL. Each delivery can
increment impressions once and clicks once; concurrent retries cannot inflate either counter.
Events and their UTC daily counters commit together, so a failed write can be retried safely.

The partner dashboard sums tracked events and externally imported totals, by account, asset,
and UTC day. CTR uses combined clicks divided by combined impressions. Live counters are
stored separately and cannot be overwritten by an import. A superuser may PUT external totals
to `/v1/partner/accounts/{account_id}/assets/{asset_id}/metrics` with
`{"day":"2026-10-07","impressions":100,"clicks":5}`. Imports replace only the external totals
for that asset/day; import external deliveries only, so the same DevFeed delivery is not
counted twice. Negative values, future dates, foreign assets, and partner-authenticated writes
are rejected. Private article relevance evaluations never count as views or clicks.

A delivery producer still needs to render placements and issue receipts before real traffic
can be measured. The signing key stays in the public API and trusted delivery servers;
Compose does not pass it to reader, extension, administration, or partner frontend services.

## Validation and rollout

Run `npm run partner:tracking:browser` to verify the shared tracker in website and built
Chrome/Edge fixture pages, including visibility timing and credential isolation. These checks
also run in reader parity CI. PostgreSQL/Redis regressions verify concurrent duplicate events,
atomic rollback, imported/live counters, and partner isolation.

Run `npm run partner:lint`, `npm run partner:test`, `npm run partner:build`, and
`npm run partner:test:browser`. Browser checks exercise the production Next build with a
controlled API fixture on desktop/mobile, role denial, partner and superuser views,
account switching, period selection, member management, and empty data states. Screenshots
are written to `reports/partner/`. Python auth tests validate signed mock-provider flows;
reporting and migration tests use disposable PostgreSQL/Redis.

The PR's unpublished `0023` migration includes accounts, memberships, assets, imported and
tracked daily metrics, and delivery deduplication alongside the catalog tables. Apply it
after master’s `0022`. An older local draft partnership `0021` or earlier local `0023` schema
must be recreated in a disposable database before retesting. Downgrade removes partner data.
Deploy the new images, configure Zitadel roles/client and the tracking signing key, and route
the portal hostname to the frontend. Production DNS/TLS/deployment, live provider validation,
placement rendering, and delivery-producer integration remain separate rollout work.
