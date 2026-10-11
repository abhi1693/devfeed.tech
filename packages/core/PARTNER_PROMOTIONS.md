# Partner recommendations and paid campaigns

Every approved product remains one canonical catalog entry. An appearance inherits its
partner account's promotion policy and may be an ordinary recommendation or a sponsored
placement. Free partners can receive relevant recommendations at every tier. An explicit
active paid campaign establishes sponsorship; a tier or the product's own pricing never does.
DevFeed records campaign agreements; it does not collect payment or infer invoice settlement.

## Configure a partner once

Assign the account in the import connection's settings. Enabled connections with active,
resolved listings establish the account's current eligible catalog. An account can have
multiple connections; the same canonical product may belong to several accounts through
their separate listings. Imported product claims still require independent qualification.

In **Partnerships → Partner accounts**, set **Promotion defaults**:

- Relevant recommendations: enabled by default, including Bronze/free partners.
- Automatically include approved products: enabled by default, including future imports.
- Allowed surfaces: articles, topic pages, and personalized feeds by default.
- Daily product frequency: three viewable impressions per product per browser per UTC day.

The **Products** tab lists that account's catalog with qualification and exclusion state.
Exclude a product to suppress every appearance for that account, including selected campaigns.
Allowing it again restores inherited policy. Global catalog exclusion, merged/withdrawn products,
stale qualification, paused accounts/connections and unresolved/withdrawn listings always win.
Allowing a product cannot override a global block or an expired qualification.

Turning off automatic inclusion prevents ordinary recommendations and whole-catalog/filter
campaigns. An explicitly selected campaign can opt in associated products. Turning off ordinary
recommendations still permits relevant paid campaigns. Account surfaces constrain both modes.

## Paid agreements

Use the account's **Campaigns** tab to create an agreement with a name, finite start/end,
status, allowed surfaces, priority and an optional campaign-wide daily impression limit.
The campaign must be active and within its UTC delivery window. Choose one targeting mode:

- **Whole catalog** includes every eligible product, including newly approved imports.
- **Selected products** uses searchable choices from that account's catalog.
- **Filters** selects verified technologies and/or approved topics. Match any value within
  each list and every populated filter dimension. Topic filters use prepared product topics.

Campaign priority breaks ties between equally relevant sponsored candidates. Payment never
bypasses the contextual threshold. An expired or paused campaign loses sponsorship immediately;
products retain ordinary eligibility if the account defaults allow it. Existing automatic
tracking records keep their original account/product/campaign identity permanently. Editing
targeting changes future delivery, and never reassigns historical measurements.

Automatic tracking associations are created idempotently after qualification and when policies
are saved. There is no routine product/ad asset classification step. Legacy manually associated
assets and imported metrics remain available as historical reporting records; they do not
silently enter the new automatic delivery pipeline.

## Context and relevance

Preparation runs in bounded background scheduler slices, independently of AI availability.
It maps technologies supported by verified official-page quotes to exact active taxonomy names
or aliases, then expands a bounded approved relationship hop. Direct support outranks related
topics. No new taxonomy or unsupported capabilities are invented.

Article appearances require a published, approved practical tutorial from an approved source,
a specific shared technology and at least two task terms present in both verified product
evidence and the publisher's original article text. Broad marketing categories alone do not
qualify. Current validated AI evaluation positives and negatives take precedence over lexical
matches for the same inputs. Those evaluations are prepared privately, never on a reader request.

Topic pages use prepared product/topic relevance. Personalized feeds use explicit topic follows
and current prepared interests from liked articles and approved topic relationships. Anonymous
requests use the page context and never manufacture an interest profile. Anonymous feed requests
without context return no products. Search, source-filtered and cursor pages suppress generic
placements. News and opinion pages receive no tutorial placements.

Requests read a bounded prepared candidate set and recheck current product, listing, account,
campaign and exclusion state. Cached matches expire after 30 minutes and are fenced by product
revision, product update time, article text/editorial revision and topic update time. New articles
are considered in each product slice; durable cursors also revisit older articles. Completed
sweeps become eligible again after five minutes. Preparation throughput depends on catalog and
article volume; missing, stale or unrelated matches result in no placement.

Each response contains at most one ordinary recommendation and one clearly labelled **Sponsored**
placement, with a factual matching reason and HTTPS destination. Canonical products are deduplicated
across partners and slots. A funded winning product uses sponsored attribution; the ordinary slot
can show the next best eligible product. No unrelated fallback fills an empty slot.

## Delivery, frequency and reporting

`POST /v1/partner-placements/query` accepts only a surface and its article/topic context plus
up to 100 excluded product IDs. `POST /v1/user/partner-placements/query` additionally uses the
authenticated browser reader's interests and requires its normal origin/CSRF checks. Clients
cannot supply another user's identity. Both return `Cache-Control: no-store` and fresh signed
delivery receipts. The shared web/Chrome/Edge component requests and tracks placements separately
from streaming article/feed content.

The reader stores product frequency, a 30-minute repeat cooldown and 90-day hiding locally under
`devfeed:partner-placement-preferences:v1`. Entries are bounded to 100 products. Impression caps
count the existing tracker definition: at least half visible for one continuous second while
the document is visible. Corrupt or unavailable preference storage suppresses placements. These
controls are local to each reader origin: website, Chrome extension and Edge extension keep
separate histories, as do different devices or cleared browser storage.

Campaign limits are enforced by a database lock and durable anonymous delivery reservations.
The daily limit counts measured impressions plus unexpired issued receipts, once per delivery,
so concurrent product requests cannot oversubscribe a campaign. Unseen reservations release
capacity when their one-hour receipt expires. Reports and campaign budgets attribute events to
the receipt's UTC issuance day. A receipt issued before midnight and viewed afterward contributes
to the previous day's metrics, so views occurring within a calendar day can exceed that day's
issuance budget. Outstanding reservations across UTC midnight remain protected until expiry.
Issuance may conservatively use capacity before a placement becomes visible.
Expired reservation rows are pruned in bounded batches after two days; durable daily counters
and historical asset attribution remain. Clicks and impressions are independently deduplicated.
No reader ID, IP or page URL is stored in
delivery reservations or tracking events. Imported external totals remain separate from live
counters and must describe external deliveries to avoid double counting.

## Enablement and rollout

Apply migration `0028` with `uv run devfeed db upgrade`. It follows the source-logo `0023` and
partner migrations `0024`–`0027`, preserves existing catalog, accounts, memberships and metrics,
and adds defaults, campaigns, exclusions, prepared matches and delivery reservations. Existing
accounts inherit recommendations and automatic inclusion; existing connections remain assigned
as before. No campaign is automatically activated. Review account policy before enabling delivery.

Set the same server-only `DEVFEED_PARTNER_TRACKING_KEY` in the public API and user API, using at
least 32 random ASCII characters. Compose injects it only into those two trusted backends.
The worker needs no signing key. An empty key disables delivery and collection. The scheduler
must run to prepare matches and automatic associations, and Redis must be available for query
and event rate limits. Frontends and extensions never receive the signing key.

Deploy all services requiring schema `0028` together after the migration. Downgrading removes new
campaign/default/matching/reservation data and automatic campaign associations; retain a database
backup before rollback. It preserves the original manually managed asset/metric data.

Validation includes disposable PostgreSQL/Redis tests for policy, concurrent caps, stale matches,
tenant boundaries and migration preservation. Run `npm run admin:test`, `npm run web:test`,
`npm run extension:test`, and the production browser checks
`node apps/admin/tests/browser/partner-accounts.mjs` and
`node apps/web/tests/browser/partner-placements.mjs` after building their apps and both extensions.
The placement and tracking browser checks also run in reader parity CI. Screenshots remain
untracked under `reports/partner-placements/`.
