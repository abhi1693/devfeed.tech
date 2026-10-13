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
the generated response validators together. The portal aliases
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

The portal follows the administration application's record and form layouts and shows all data
belonging to the signed-in user's active account memberships. There is no global account selector
or account UUID prefix in workspace URLs. The shared reader-style sidebar starts with compact icons on desktop,
expands with labels using the bottom toggle, and remembers that choice for this portal. On mobile,
Navigation opens the labeled menu and closes after navigation or Escape without changing the desktop preference.

The header theme button cycles through system, light, and dark appearances. The portal remembers
the selection in this browser, follows device appearance changes in system mode, and applies the
saved appearance before rendering the page. Theme controls, styles, and browser preferences are
shared with the reader. The sign-in page retains its light partner palette.

- `/`: Dashboard with aggregate performance totals and a daily trend across accessible accounts.
- `/performance`: daily impressions, clicks and CTR charts, format comparisons, sortable product
  results with an Account column, and a daily CSV export. Missing ledger days remain gaps, and
  CSV cells remain blank; observed zero counts remain zero. Period CTR uses total clicks divided
  by total impressions. Dates use UTC.
- `/products`: the searchable manual and imported catalog includes products without delivery
  measurements, Account and Source columns, and a searchable account filter in the Partner account
  column header. It starts with all accounts and preserves product search and archive filters when
  changing accounts. `/products/new` adds a product directly to
  an authorized account without an import connection. Product names open `/products/<product-id>`;
  `/products/<product-id>/edit` edits manually managed facts and `/products/<product-id>/archive`
  archives a manual entry. Placement actions open `/products/<product-id>/placements`.
  The optional `account_id` query selects an account's policy
  for a shared product and is authorized independently. Previews appear inline. Cancel discards
  the draft; a confirmed save returns to the product record.
- `/connections`: imports across accessible accounts, with full-page detail, create, edit and
  disconnect workflows. The account relationship is selected when creating a connection.
- `/settings`: a searchable, paginated account table. `/settings/<account-id>` opens that account's
  General section with account details, tier, promotion policy and benefits. Underlined section
  navigation follows the reader settings structure: `/settings/<account-id>/destination` edits
  click destinations, `/settings/<account-id>/formats` edits automatic ad formats and creative
  previews, and `/settings/<account-id>/audience` edits audience targeting. Only the active section
  loads its controls; every section checks account access and preserves the settings list context.

Delivery charts overlay impressions and clicks with labeled left and right scales, a shared
tooltip and distinct line styles. Unmeasured days remain gaps in both series.
Product placement settings can override the website's OG image with a public HTTPS image URL
or a JPEG, PNG or WebP upload up to 5 MB. Select Custom image to use either option;
Website image hides both controls and uses the default OG image.
Uploads save immediately, like profile avatars,
without saving other placement edits. Their proportions are preserved and embedded metadata is
removed. Replacing or clearing an upload reuses or removes a bounded set of managed image files.
Uploaded files under `partner-ad-images/` use `Cache-Control: public, max-age=0, must-revalidate`
and a fresh `?v=` revision. Preserve this cache policy rather than forcing immutable caching
for that prefix.
The override belongs to the account and product, survives catalog imports, and applies to automatic
and manually selected product ads that display an image. Clearing it restores the website image;
sponsored articles retain their article image. The editor previews an unsaved URL privately, while
reader placements only serve processed, managed images. The website image remains the fallback
while processing is queued or fails. Previews refresh during processing with bounded polling and
offer a manual refresh. Saving an unchanged failed image retries processing.

Uploads use the existing `DEVFEED_IMAGE_STORAGE_*` settings and `DEVFEED_IMAGE_PUBLIC_URL`.
Compose passes storage credentials only to the images worker and private upload APIs;
frontends and the public reader API receive no storage credentials. The public image URL must
use HTTPS. No additional upload environment variables are required.
The dashboard keeps totals, delivery trends and the format breakdown together in a compact
laptop layout. Related settings and import forms share desktop columns and stack on mobile.
Forms use full pages. Headings, record panels, tables, controls and charts are imported directly
from `@devfeed/ui` by both applications. Obsolete account-prefixed frontend routes are removed.
Import authentication and pagination use the shared keyboard-accessible dropdowns; scheduled
imports and credential replacement use the shared checkboxes. Unavailable authentication choices
remain visible and disabled when credential storage is not configured.
The connection editor shares the admin configuration fields and shows a live API response beside
the form. Colored checks link each mapped field to its JSON path; paths absent from a sample are
identified without being treated as invalid. Mapping edits reuse the current sample. Request and
authentication changes clear stale samples, and changing the API origin clears a draft credential.
“Test import connection” previews up to three mapped products without saving settings, enrolling
products or queuing imports. Both previews use account-scoped, CSRF-protected endpoints with
bounded upstream requests, rate limits and redacted responses. Existing stored credentials remain
write-only and can only be reused for their bound API origin and authentication settings.
Record IDs remain where they identify the product, connection or account being edited.

Workspace reporting uses the full membership scope in SQL; account-list pagination never limits
report totals, products or connections. Record access checks membership directly, so a settings
bookmark remains available beyond the first account page or after accounts are reordered.
Membership removal takes effect on the next request. A role alone does not expose other partners'
data. Reporting periods, sorting and pagination survive navigation and reloads. On Performance,
choose a column header to sort the full result set before pagination. Name starts in ascending
order; a metric starts in descending order. Choose the same header again to reverse direction.
Unavailable metrics appear last in either direction. Changing the sort or reporting period returns
to the first result page.

Account administration, memberships, tiers, benefits, campaigns and historical asset management
live in the admin app at `/partnerships/accounts`. These operations use
`/v1/admin/partner-accounts`, require an admin session with the `superuser` role, and enforce the
admin origin and CSRF token. The partner API exposes account, dashboard, inventory and preview
GET routes, plus account-scoped updates for product click destination, automatic ad formats,
eligible audience defaults and owned product placement settings. Members cannot manage campaigns
or mutate reports. Sign-out uses POST.
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

The dashboard and Performance page show impressions, clicks, CTR, per-product results, daily
activity and a 7/30/90/365 day selector. Account records show tier and benefits. Periods use inclusive UTC calendar dates through today.
CTR is total clicks divided by total impressions, not an average of daily percentages.
Summary totals include all assets even when the asset table is paginated. A day with no
measurement is distinct from a measured zero; coverage and latest update are visible.
Revenue, conversions, and financial ROI are not claimed by these metrics.

### Native advertising formats

Silver, Gold, Platinum and Diamond support native feed cards, homepage sponsorship, sponsored
content and contextual engagement ads. Account settings choose automatic feed, homepage and
engagement formats. Automatic formats default to Feed until another format is enabled.
Homepage sponsorship appears as a compact unit beneath the main feed controls, only in the
unfiltered home feed. Engagement placements use grounded task matches or direct topic support in
existing grid and preview areas. They do not interrupt article reading or replace reader actions.
A Feed card without managed artwork uses compact product branding; Engagement uses a compact
logo, product copy and an Explore affordance. Feed and article-search
lists with fewer than four organic stories omit in-list placements.

Sponsored content requires a dated campaign linked to an existing approved, visible article.
Its card uses that article's title, image, topics and publisher and opens the article preview.
Activating the campaign adds a permanent sponsor disclosure to the article, including ordinary
article cards and previews after the campaign ends. Campaign creation does not publish articles.
The sponsored article is omitted when it is already present in the visible feed.

All product placements show a distinct Sponsored pill, regardless of tier or internal promotion
kind, and cannot be dismissed. The formats share the existing maximum of two placements per
delivery response. Article, topic, category and search contexts require a relevant match.
Generic Feed sponsorship may fall back only on the unfiltered home feed without supplied article
context or resolved reader interests; failed contextual matching cannot broaden into unrelated
Feed inventory. Explicit Homepage sponsorship remains a separate brand placement on the
unfiltered home feed and may appear alongside page context. Audience restrictions also apply
to fallback delivery. Formats have separate assets and attribution while the product performance
table combines the same product's formats within each account into one row.

**Creative preview** shows a real accepted product or approved sponsored article for the selected
format without reserving delivery or recording measurements. From a product's placement page,
it requests that exact account-owned product and uses the saved click destination. Excluded or otherwise unavailable
inventory has no preview. Unsaved placement edits do not affect preview availability, and an
appearance preview does not confirm delivery eligibility. It is separate from the compact URL-only
**Click destination preview**.

Products can inherit account placement locations and formats or use explicit allowlists.
These restrictions apply to automatic delivery and dated campaigns. Empty lists disable all
locations or formats; choosing **Inherit account settings** restores the defaults. A product
cannot enable an automatic format that the account has disabled or bypass a tier entitlement,
account exclusion or global exclusion. Allowing sponsored content makes the product eligible
for a campaign with an approved article; it does not create or publish an article.
Members can edit these settings for products in their account, whether added manually or imported,
independently of performance
reporting. The partner placement form and admin dialog can explicitly Allow or Exclude a product while retaining
its format and location restrictions. They distinguish unsaved choices from saved settings and
show the automatic formats and locations permitted within account defaults. Allowing a product
does not override account pauses, automatic inclusion, qualification or targeting. A save is
confirmed only when the response belongs to the requested account and product and confirms the
submitted exclusion and restrictions; failed or mismatched saves retain the draft for review.
Canceling the partner form or closing the admin dialog discards unsaved changes.

### Manual products

Organizations can advertise their products without managing a launch platform or connecting an
external API. Add a name, public HTTPS website, description and pricing under an active partner
account. Creation queues the same background website checks, capability assessment, imagery and
contextual preparation used by imports. Account approval, tier, audience and placement policies
continue to apply. Manually supplied descriptions do not grant approval or verified capabilities.

The catalog keeps a canonical identity for each website. Adding an existing product gives the
account its own inventory entry and placement settings; it does not overwrite that product's
shared facts. Only the account that manages a manual product's canonical facts can edit them.
Edits require the current revision and invalidate outdated assessment and contextual matches;
unchanged saves preserve them.

Archiving removes the account's manual association and preserves reports. Select **Show archived
products** in Products, open the record and restore it there. An active imported listing can
independently keep the same product available, so aggregate availability and the manual entry's
state are reported separately. The `include_archived` inventory option is off by default so
placement pickers continue to offer active products.
The account-level product exclusion blocks placements regardless of their source. Direct products
use their website when the account's preferred listing destination has no owned launch listing.

Manual products use the existing partner sessions, roles and account memberships. They do not
create a launch-platform connection, grant membership or change an account's advertising tier.
No additional environment variables are required.

### Import connections

Members can create and manage REST/JSON imports for any of their active accounts. Connection IDs
are generated by the server; members cannot claim an existing provider identifier or reassign
a connection to another account. Settings include public HTTPS endpoints, JSON field mappings,
pagination and a sync interval of 15 minutes to seven days. Imports run in the background;
the detail record shows connection state, product counts and recent runs. Sync, pause and resume
actions check the current revision, and conflicting edits preserve the draft for review.

Connections support public endpoints, bearer tokens and API keys. Tokens are write-only:
editing a connection never retrieves the saved value. Keeping authentication unchanged preserves
the token; changing the origin or authentication requires a replacement. Selecting no authentication
removes the saved token. Administrator-managed environment credentials remain under administration
control and cannot be redirected or replaced through the partner portal.

Set `DEVFEED_PARTNER_CREDENTIAL_KEY` to a persistent Fernet key in the partner API and import worker.
Generate one with `uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
and store it in private deployment configuration. The key is separate from database ciphertext;
back it up securely because replacing or losing it requires re-entering stored credentials.
Credentials are encrypted and bound to account, connection, HTTPS origin and authentication.
Job snapshots, API responses and logs exclude token values and ciphertext. Without the key,
public imports remain available and private credential setup is unavailable.

The disconnect confirmation is a dedicated page. Disconnect stops imports, removes the saved
token and archives the connection; catalog records, measurements and import history remain.
Archived sources cannot deliver placements or resume imports, and no longer consume the account's
25-connection limit. Creating a new connection does not bypass qualification or audience rules.

### Contextual delivery and tracking

Article placements and feed Engagement require current grounded tutorial task matches. Topic
Engagement requires directly verified technology support; a broad shared category or a related
topic alone cannot qualify it. Matches must still describe the current product and article inputs,
and a validated negative task evaluation prevents task-based delivery for that product/article
pair. Within each delivery phase, grounded task intent ranks ahead of broader topic support,
followed by match score and campaign priority.
Locked live-policy checks remain authoritative before issuing a receipt.

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
Mounted pages also enforce expiry: expired cards become inactive and stop tracking, and any
organic story hidden by a sponsored-content card returns. Reading-anchor restoration preserves
the article being read as the layout changes. Tabs recheck expiry when they become visible or
regain focus; returning from a preview does not renew an expired receipt.

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

The unpublished partnership migration `0024` creates accounts, memberships, catalog, import
connections, campaigns, promotion policies, audiences, native formats, prepared matches and daily
reporting after master's source-logo revision `0023`. It also adds managed product logos/images,
bounded enrollment jobs, manual account products, provider-free assessment jobs, tracking
deduplication and reservations, product delivery restrictions
and permanent article sponsor disclosures. New creative identities receive separate assets so
historical metrics stay attached to their original format and article.

Apply it before starting the updated services. Downgrading to `0023` removes all partnership data;
existing reader data and article/topic/source image jobs remain. Configure Zitadel roles/client,
the tracking signing key and the portal hostname when deploying. Production DNS/TLS/deployment,
live provider validation and paid-agreement activation remain separate rollout work.

## Admin account pages

Superusers manage partnerships at `/partnerships/accounts` in the admin app. Accounts use
the common admin table, record detail, and create/edit form components. Select an account
to view its tier, benefits and promotion defaults. Related objects manages members and historical
reporting assets. Manual and imported products inherit their account's defaults; Products manages exclusions,
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
placements and contextless sponsored Feed fallback in the unfiltered main feed. Silver and higher
tiers also permit additional dated campaigns. Grounded task matches rank ahead of broader topic
support within each delivery phase; search, category, article and topic placements require a
relevant match. Gold adds product promotion planning. Platinum adds audience targeting and priority campaign
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

Account tiers use the canonical Bronze, Silver, Gold, Platinum and Diamond values. Benefits
are derived from tier policy rather than stored as custom database text.

Membership forms use the common searchable Users picker, scoped to the admin’s Zitadel
issuer and organization. Choose a user by name; the API resolves their immutable identity.
The account members table shows registered names and emails, links to admin user records,
and retains issuer/subject details when no matching profile exists. Admin user details also
link to all assigned partner accounts under Account, including paused partnerships.
Partner sign-in registers the verified user in DevFeed, even before membership is assigned.
Existing partner sessions register on the next portal identity request. Reader sign-in also
registers users. The partner role in Zitadel is required before accessing an assigned account. Membership assignment
does not grant or change Zitadel roles.

Dashboard shows performance, partnership tier and included benefits. Settings contains account
configuration. **Product click destination** lets members choose the product website or their connected
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
connection belongs to one account; an account may own multiple connections. The data model permits
unassigned records, which do not establish account catalog ownership. Administration forms require
an account; portal connections always belong to the current authorized account.
Assignment changes use revision protection and preserve historical assets, memberships, AI
evaluations and sync scheduling. The live assignment controls future eligibility and attribution.
The connection detail links to its assigned admin account.
