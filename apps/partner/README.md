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
issuer, and role claim policy as administration. Create the `partner` project role and assign it to portal users.
The API requests the partner role scope and checks authenticated role
claims against the configured organization. An ordinary reader role does not grant portal
access. The partner API reads `DEVFEED_OIDC_CLIENT_ID` / `DEVFEED_OIDC_CLIENT_SECRET`; Compose
maps `DEVFEED_PARTNER_OIDC_CLIENT_ID` / `DEVFEED_PARTNER_OIDC_CLIENT_SECRET` into those values.
Use a separate process environment when launching the API directly.

Portal access requires the exact `partner` role, and every identity must have an active
account membership to see data. Memberships use the validated issuer and immutable Zitadel
subject ID. Role grants alone show an empty account state; the partner portal has no global
superuser reporting bypass. Membership deletion and account pausing take effect on the next
request. Unavailable or foreign accounts return 404.

The portal has separate account pages: `/<account-id>` for the partnership overview,
`/<account-id>/performance` for daily activity, and `/<account-id>/assets` for products and ads.
The account path and reporting period survive navigation and reloads.

All account, membership, tier, benefit, and asset management lives in the admin app at
`/partnerships/accounts`. These operations use `/v1/admin/partner-accounts`, require an admin
session with the `superuser` role, and enforce the admin origin and CSRF token. The partner
API exposes only account-list and dashboard GET routes; its frontend rejects report
mutations. Only sign-out uses POST. Reader, partner, and admin sessions are isolated.

Assign project roles in Zitadel before adding account memberships in administration. Role
grants are captured at login and bounded by the session lifetime; revoke Redis sessions for
immediate role revocation. The admin and partner clients must use the same issuer for
memberships to match.

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
to `/v1/admin/partner-accounts/{account_id}/assets/{asset_id}/metrics` with
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
controlled API fixture on desktop/mobile, role denial, partner reporting routes,
searchable account selection, period selection, and empty data states. Admin browser checks
cover the standard account list/detail/create/edit pages and related membership/asset forms. Screenshots
are written to `reports/partner/`. Python auth tests validate signed mock-provider flows;
reporting and migration tests use disposable PostgreSQL/Redis.

The PR's unpublished `0023` migration includes accounts, memberships, assets, imported and
tracked daily metrics, and delivery deduplication alongside the catalog tables. Apply it
after master’s `0022`. An older local draft partnership `0021` or earlier local `0023` schema
must be recreated in a disposable database before retesting. Downgrade removes partner data.
Deploy the new images, configure Zitadel roles/client and the tracking signing key, and route
the portal hostname to the frontend. Production DNS/TLS/deployment, live provider validation,
placement rendering, and delivery-producer integration remain separate rollout work.

## Admin account pages

Superusers manage partnerships at `/partnerships/accounts` in the admin app. Accounts use
the common admin table, record detail, and create/edit form components. Select an account
to view its tier and benefits; use Related objects to add or remove members and associate
products or ads. Every form has a real URL and returns to its account after saving.

## Partnership tiers and benefits

Tiers are Bronze, Silver, Gold, Platinum, and Diamond, in ascending order. The admin
selector and API accept only these tiers (stored as lowercase identifiers). Benefits
are cumulative and defined in `packages/core/src/partner_tiers.py`; they cannot be edited
per account or submitted to the API. Create/edit forms preview the selected tier’s
benefits from the same authenticated API catalog. Bronze includes portal access and reporting; Silver
adds product placement opportunities; Gold adds sponsored campaign opportunities; Platinum
adds priority campaign support; Diamond adds custom partnership planning. These are starter
commercial offerings, independent of role and membership authorization.

Migration `0024` normalizes existing recognized tiers, maps unrecognized draft labels to
Bronze, and removes the database benefits column. Review account tiers after upgrading.
Downgrading recreates an empty benefits column; it does not restore old custom benefit text.

Membership forms use the common searchable Users picker, scoped to the admin’s Zitadel
issuer and organization. Choose a user by name; the API resolves their immutable identity.
Partner sign-in registers the verified user in DevFeed, even before membership is assigned.
Existing partner sessions register on the next portal identity request. Reader sign-in also
registers users. The partner role in Zitadel is required before accessing an assigned account. Membership assignment
does not grant or change Zitadel roles.

The overview shows partnership tier and benefits; measured outcomes and daily activity
are on Performance. Account selection appears only for users with multiple memberships.
A single account is selected automatically on every reporting page.

The navbar account dropdown shows user identity and partner-page links, with sign out
inside the menu. It supports keyboard navigation, Escape dismissal, and compact mobile
avatars using the common UI dropdown components.

Reporting period and account-list pagination remain query parameters. `/` opens the default
account. Account paths require membership; the old `/performance`, `/assets`, and `?account=`
URLs return 404.
