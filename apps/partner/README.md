# DevFeed partner portal

The standalone Next.js portal is intended for `https://partner.devfeed.tech`. It has its own
Python API in `apps/partner-api`, separate from the public reader, user API, and admin API.
Run `npm run partner:dev` (port 3002) and
`uv run uvicorn devfeed_partner_api.main:app --port 8004 --no-proxy-headers --no-access-log`.
Set `DEVFEED_PARTNER_API_URL=http://localhost:8004` and
`DEVFEED_PARTNER_BASE_URL=http://localhost:3002` for both processes. The API also needs the
normal database/Redis settings and `DEVFEED_PARTNER_COOKIE_SECURE=false` for local HTTP.
Compose and its build overlay include both services; only the frontend port is exposed.

## API contracts

Run `npm run partner:generate` after changing the partner API response or request models.
The exporter builds OpenAPI from `devfeed_partner_api.main.create_app()` using normal application
configuration, without starting services or querying the database. Commit `openapi.json`, generated TypeScript models and
the four generated preference/preview/topic response validators together. The portal aliases
these generated output types rather than maintaining a second set of account/report models;
computed account fields remain separate from preference input.

Orval's runtime response projection requires every serialized account field and audience
array, removes schema defaults, and restricts automatic formats using backend-exported
metadata. `parseAccount` also checks the requested account ID and rejects duplicate formats.
A partial or malformed save response therefore cannot confirm a successful preference save.
The original OpenAPI snapshot retains the backend's input/output and default semantics.

Generation uses explicit paths under `src/lib/api/generated` with Orval cleanup disabled.
`npm run partner:test` runs the contract regression, including two isolated generations that
verify deterministic output and preservation of source, dependency and contract files.
Release version checks and bumps cover both admin and partner snapshots and generated files;
a failed generation restores both sets of artifacts.

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
The account path and reporting period survive navigation and reloads. Opening an account URL
checks its current membership directly, independently of the account picker's 100-item pages.
Bookmarked accounts remain available after account reordering or without `account_offset`.
Paging the picker retains the selected account and report until another account is chosen.
On Products & ads, choose Name, Impressions, Clicks or CTR to sort the full result set
before pagination. Name starts in ascending order; a metric starts in descending order.
Choose the same header again to reverse direction. Unavailable metrics appear last in
either direction. Sorting and the current asset page survive navigation and reloads;
changing the sort or reporting period returns to the first asset page.

All account, membership, tier, benefit, and asset management lives in the admin app at
`/partnerships/accounts`. These operations use `/v1/admin/partner-accounts`, require an admin
session with the `superuser` role, and enforce the admin origin and CSRF token. The partner
API exposes account-list, individual account and dashboard GET routes plus an account-scoped PUT
preferences route for product click destination, automatic ad formats and eligible audience defaults. Report mutations remain
unavailable. Sign-out uses POST.
Reader, partner, and admin sessions are isolated.

The gateway accepts request bodies up to 1,000,000 bytes and requires the whole upload to
complete within two seconds. Incomplete uploads return 408 and oversized uploads return 413
without reaching the partner API. Body reading uses the shared `@devfeed/http` helper;
completed requests retain a separate 45-second upstream timeout.

Assign project roles in Zitadel before adding account memberships in administration. Role
grants are captured at login and bounded by the session lifetime; revoke Redis sessions for
immediate role revocation. The admin and partner clients must use the same issuer for
memberships to match.

## Measured value

Accounts own `partner_assets` (ordinary recommendations or sponsored appearances). Automatic
assets reference canonical products and optional explicit paid campaigns. Assigned import
connections establish each account's eligible catalog; canonical identity stays shared.
Each asset belongs to one account, keeping reporting attribution stable. Shared canonical
products can have distinct assets for different partner accounts. Membership, ownership,
and metrics are never inferred from imported emails/domains. Silver and higher tiers default to
sponsored placement through an automatically managed standing campaign; account overrides can
select product recommendations. Product pricing does not determine sponsorship. Trusted import
acceptance is an explicit account policy and remains separate from factual capability assessments.

The dashboard shows impressions, clicks, CTR, per-asset results, daily activity, a 7/30/90/365
day selector, tier, and benefits. Periods use inclusive UTC calendar dates through today.
CTR is total clicks divided by total impressions, not an average of daily percentages.
Summary totals include all assets even when the asset table is paginated. A day with no
measurement is distinct from a measured zero; coverage and latest update are visible.
Revenue, conversions, and financial ROI are not claimed by these metrics.

### Native advertising formats

Silver, Gold, Platinum and Diamond support native feed cards, homepage sponsorship, sponsored
content and contextual engagement ads. Account settings choose automatic feed, homepage and
engagement formats. Existing accounts retain feed-only settings until another format is enabled.
Homepage sponsorship appears as a compact unit beneath the main feed controls, only in the
unfiltered home feed. Engagement placements use relevant products in existing grid and preview
areas. They do not interrupt article reading or replace reader actions.

Sponsored content requires a dated campaign linked to an existing approved, visible article.
Its card uses that article's title, image, topics and publisher and opens the article preview.
Activating the campaign adds a permanent sponsor disclosure to the article, including ordinary
article cards and previews after the campaign ends. Campaign creation does not publish articles.
The sponsored article is omitted when it is already present in the visible feed.

All product placements show a distinct Sponsored pill, regardless of tier or internal promotion
kind, and cannot be dismissed. The formats share the existing maximum of two placements per
delivery response. Article, topic, category and search contexts require a relevant match;
only sponsored feed delivery can fall back in the unfiltered home feed. Audience restrictions
also apply to fallback delivery. Formats have separate assets and attribution while the product
performance table combines the same product's results into one row.

**Creative preview** shows a real accepted product for the selected format without recording
an impression. It is separate from the compact URL-only **Click destination preview**.

Products can inherit account placement locations and formats or use explicit allowlists.
These restrictions apply to automatic delivery and dated campaigns. Empty lists disable all
locations or formats; choosing **Inherit account settings** restores the defaults. A product
cannot enable an automatic format that the account has disabled or bypass a tier entitlement,
account exclusion or global exclusion. Allowing sponsored content makes the product eligible
for a campaign with an approved article; it does not create or publish an article.
Members can edit these settings for their own imported products independently of performance
reporting. Changing one setting preserves other saved product settings.

### Contextual delivery and tracking

Deduplication events retain the current UTC date and two complete prior dates. The scheduler
removes older events and delivery reservations older than 48 hours in independent, indexed
batches of at most 4,000 rows per table per pass. Busy rows wait for a later pass. Daily
tracked and imported reporting totals remain intact; expired receipts cannot create new
measurements after their deduplication rows are removed.

The website and both extensions share `observePartnerPlacement(element, receipt)` in
`apps/web/src/lib/partner-tracking.ts`. The shared contextual placement component attaches it
to clickable products and cleans it up on unmount. It counts an impression after at
least half the placement stays visible for one continuous second while the document is
visible. It counts a click independently, including keyboard and middle-button activation.
Retries use the same receipt; errors never block link navigation. Products inherit account
defaults and relevant campaign targeting. See [Partner promotions](../../packages/core/PARTNER_PROMOTIONS.md).

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

The public and user APIs issue fresh receipts with durable anonymous delivery reservations.
The signing key stays in these two trusted backends;
Compose does not pass it to reader, extension, administration, or partner frontend services.

## Validation and rollout

Run `npm run partner:tracking:browser` to verify the shared tracker in website and built
Chrome/Edge fixture pages, including visibility timing and credential isolation. These checks
and `node apps/web/tests/browser/partner-placements.mjs` run in reader parity CI. PostgreSQL/Redis
regressions verify concurrent duplicate events,
atomic rollback, imported/live counters, and partner isolation.

Run `npm run partner:lint`, `npm run partner:test`, `npm run partner:build`, and
`npm run partner:test:browser`. Browser checks exercise the production Next build with a
controlled API fixture on desktop/mobile, role denial, partner reporting routes,
searchable account selection, period selection, and empty data states. Admin browser checks
cover the standard account list/detail/create/edit pages and related membership/asset forms. Screenshots
are written to `reports/partner/`. Python auth tests validate signed mock-provider flows;
reporting and migration tests use disposable PostgreSQL/Redis.

The PR's unpublished `0024` migration includes accounts, memberships, assets, imported and
tracked daily metrics, and delivery deduplication alongside the catalog tables. Apply it
after master’s source-logo migration `0023`. An older local draft partnership schema
must be recreated in a disposable database before retesting. Downgrade removes partner data.
Deploy the new images, configure Zitadel roles/client and the tracking signing key, and route
the portal hostname to the frontend. Production DNS/TLS/deployment, live provider validation,
and paid-agreement activation remain separate rollout work. Migration `0028` adds promotion
policies, campaigns, exclusions, prepared matches and delivery reservations; apply it before
deploying the new services. Existing account, membership, catalog and manual reporting data survive.
Migration `0034` adds native formats, per-product delivery restrictions and permanent article
sponsor disclosures. Apply it before starting the updated services. Existing campaigns and
reporting assets retain the feed format; new creative identities receive separate assets so
historical metrics are not reassigned when a campaign's format or article changes.

## Admin account pages

Superusers manage partnerships at `/partnerships/accounts` in the admin app. Accounts use
the common admin table, record detail, and create/edit form components. Select an account
to view its tier, benefits and promotion defaults. Related objects manages members and historical
reporting assets. Products inherit defaults from assigned connections; Products manages exclusions,
and Campaigns manages paid whole-catalog, selected-product or filtered agreements. Named pickers
use the account's catalog and approved taxonomy. Automatic attribution cannot be reassigned.
Every form has a real URL and returns to its account after saving.

## Partnership tiers and benefits

Tiers are Bronze, Silver, Gold, Platinum, and Diamond, in ascending order. The admin
selector and API accept only these tiers (stored as lowercase identifiers). Benefits
are cumulative and defined in `packages/core/src/partner_tiers.py`; they cannot be edited
per account or submitted to the API. Create/edit forms preview the selected tier’s
benefits from the same authenticated API catalog. Bronze includes portal access, reporting and
relevant recommendations; Silver adds the four native advertising formats, inherited sponsored
placements and sponsored fallback in the unfiltered main feed. Silver and higher tiers also permit
additional dated campaigns. Contextual matches rank first; search, category, article, and topic
placements require a relevant match. Gold adds product promotion planning. Platinum adds audience targeting and priority campaign
support; Diamond includes those benefits and custom partnership planning. These are starter
commercial offerings, independent of role and membership authorization.

### Audience targeting

Platinum and Diamond can set audience defaults by experience level, programming languages,
and developer tools. Automatic placements inherit these defaults. Dated campaigns can
inherit the account audience or use a custom audience, independently of which products the
campaign includes. An empty dimension means any value; selected values match with OR within
each dimension and AND across dimensions.

Language and tool selections reference active catalog topics. Matching uses signed-in readers'
current developer stacks (primary, hobby, or learning), followed topics, and fresh inferred
interests. Past stack entries and stale interest generations do not qualify. Experience level
is optional, self-reported in reader settings, and private. It is not inferred from a biography
or a technology's using-since year. Partners receive performance totals, never individual
reader attributes.

Readers without the required audience signals, including guests, do not qualify for restricted
placements. Unrestricted placements remain available. Audience criteria also apply to sponsored
feed fallback and do not replace contextual relevance checks for previews, search, or categories.
Removing a tier entitlement stops restricted delivery rather than broadening the audience.

Migration `0025` normalizes existing recognized tiers, maps unrecognized draft labels to
Bronze, and removes the database benefits column. Review account tiers after upgrading.
Downgrading recreates an empty benefits column; it does not restore old custom benefit text.

Membership forms use the common searchable Users picker, scoped to the admin’s Zitadel
issuer and organization. Choose a user by name; the API resolves their immutable identity.
The account members table shows registered names and emails, links to admin user records,
and retains issuer/subject details when no matching profile exists. Admin user details also
link to all assigned partner accounts under Account, including paused partnerships.
Partner sign-in registers the verified user in DevFeed, even before membership is assigned.
Existing partner sessions register on the next portal identity request. Reader sign-in also
registers users. The partner role in Zitadel is required before accessing an assigned account. Membership assignment
does not grant or change Zitadel roles.

The overview shows partnership tier, benefits, inherited promotion type, and import acceptance.
**Product click destination** lets members choose the product website or their connected
partner listing. Both destinations include DevFeed UTM attribution. A product without a partner
listing is omitted when that destination is selected. The account-scoped preferences endpoint
accepts independent destination, automatic-format and Platinum/Diamond audience settings, requires the session
CSRF token, and rechecks membership. Members cannot change account tiers or entitlements.
Measured outcomes and daily activity
are on Performance. Account selection appears only for users with multiple memberships.
A single account is selected automatically on every reporting page.

The navbar account dropdown shows user identity and partner-page links, with sign out
inside the menu. It supports keyboard navigation, Escape dismissal, and compact mobile
avatars using the common UI dropdown components.

Reporting period and account-list pagination remain query parameters. `/` opens the default
account. Account paths require membership; the old `/performance`, `/assets`, and `?account=`
URLs return 404.

Admin import connection settings require a searchable **Partner account** selection. Each
connection belongs to one account; an account may own multiple connections. The backend retains
a nullable account reference for legacy/API compatibility, so older or API-created rows can
remain unassigned. Creating or editing a connection in administration requires an account.
Assignment changes use revision protection and preserve historical assets, memberships, AI
evaluations and sync scheduling. The live assignment controls future eligibility and attribution.
The connection detail links to its assigned admin account.
