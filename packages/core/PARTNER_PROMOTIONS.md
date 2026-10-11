# Partner recommendations and paid campaigns

Every approved product remains one canonical catalog entry. Every displayed product placement
carries a **Sponsored** disclosure and has no dismissal control, regardless of tier or campaign.
Its allocation and reporting inherit the partner account's promotion policy: Bronze
defaults to relevant recommendations without a campaign. Silver, Gold, Platinum and Diamond default to
campaign-backed delivery, with an optional account override to ordinary recommendations.
A product's retail pricing never establishes a paid partnership.
DevFeed records campaign agreements; it does not collect payment or infer invoice settlement.

## Configure a partner once

Assign the account in the import connection's settings. Enabled connections with active,
resolved listings establish the account's current eligible catalog. An account can have
multiple connections; the same canonical product may belong to several accounts through
their separate listings. Official-page facts and matching evidence remain independently qualified. Administrators can
choose trusted import acceptance for an account: all its active, resolved imports then receive
commercial acceptance, including non-developer products, without changing their AI assessment
or inventing verified technologies. Acceptance belongs to that account/product association.

In **Partnerships → Partner accounts**, set **Promotion defaults**:

- Promotion type: inherit the tier, or choose an entitled type for this account.
- Import acceptance: independently qualified by default, or trusted partner imports.
- Relevant recommendations: enabled by default for the ordinary promotion type.
- Automatically include approved products: enabled by default, including future imports.
- Allowed surfaces: articles, topic pages, and personalized feeds by default.

The **Products** tab lists that account's catalog with qualification and exclusion state.
Exclude a product to suppress every appearance for that account, including selected campaigns.
Allowing it again restores inherited policy. Global catalog exclusion, merged/withdrawn products,
stale qualification, paused accounts/connections and unresolved/withdrawn listings always win.
Allowing a product cannot override a global block. Trusted commercial acceptance can admit
inventory without fresh developer qualification; article/topic relevance still requires current
grounded matching data.

Turning off automatic inclusion prevents ordinary recommendations and whole-catalog/filter
campaigns. An explicitly selected campaign can opt in associated products. Turning off ordinary
recommendations still permits relevant paid campaigns. Account surfaces constrain both modes.

## Paid agreements

Silver and higher accounts inherit a system-managed agreement for each enabled automatic format
covering current and future imports. It is controlled through account defaults, with stable
attribution for reporting; its technical end date does not represent an invoice or billing term.
Pausing the account, disabling automatic inclusion, or changing its promotion type stops this
inherited delivery immediately. Changing its tier immediately changes its allowed promotion types.

Use the account's **Campaigns** tab to create an agreement with a name, finite start/end,
status, allowed surfaces, priority and an optional campaign-wide daily impression limit.
The campaign must be active and within its UTC delivery window. Choose one targeting mode:

- **Whole catalog** includes every eligible product, including newly approved imports.
- **Selected products** uses searchable choices from that account's catalog.
- **Filters** selects verified technologies and/or approved topics. Match any value within
  each list and every populated filter dimension. Topic filters use prepared product topics.

Campaign priority breaks ties between equally relevant sponsored candidates. Explicit active
agreements own their selected products within the same format so inherited delivery cannot bypass their daily cap.
Article and topic placements always require a contextual match. An expired or paused dated campaign stops its own delivery immediately;
products retain ordinary eligibility if the account defaults allow it. Existing automatic
tracking records keep their original account/product/campaign identity permanently. Existing active
dated campaigns created before migration 0031 retain their original delivery window even on lower
tiers; new or edited paid agreements require Silver or higher. Editing
targeting changes future delivery, and never reassigns historical measurements.

Automatic tracking associations are created idempotently after qualification and when policies
are saved. There is no routine product/ad asset classification step. Legacy manually associated
assets and imported metrics remain available as historical reporting records; they do not
silently enter the new automatic delivery pipeline.

## Native ad formats and product controls

Silver and higher accounts can use Feed, Homepage sponsorship, Sponsored content and Engagement.
Existing accounts retain Feed as their only automatic format. Account defaults and partner-portal
preferences can enable Feed, Homepage sponsorship and Engagement; an empty selection stops all
standing sponsored channels. Sponsored content requires a dated campaign and a selected approved,
visible article. It never publishes an article, rewrites its text or changes its byline.
Newsletter delivery is not included; Agent Ads remain deferred.

Homepage sponsorship appears only on an unfiltered main feed that enables sponsored fallback.
Engagement remains strictly contextual on supported article, topic and feed locations. Feed and
Homepage sponsorship may use the explicit main-feed fallback. Sponsored content is feed-only,
uses the campaign's relevant account product for eligibility, requires its own approved topics to match
the current feed, and opens the actual article preview. The article picker offers only visible
stories with active primary/supporting topics; untagged or unrelated stories fail closed.
An article already visible in the feed is not injected again. All formats share existing relevance,
audience, account acceptance and campaign-wide impression budgets.

A product's scoped promotion settings can restrict formats and locations for both automatic and
explicit campaign delivery. Null inherits the account; an empty list blocks all values in that
dimension. Product settings cannot activate a disabled automatic account channel, grant a missing
tier entitlement or override global exclusion. Partial updates preserve omitted settings.
Identity merges intersect the two products' restrictions and preserve exclusions.

Activating a sponsored-content campaign records the sponsor name permanently on the article.
The disclosure appears on ordinary cards, previews and search results after the agreement expires
or its product rules change. Concurrent sponsors are merged under an article row lock; publication
and search caches are invalidated with the committed change. The creative's article and format
are immutable asset snapshots so changing the campaign cannot reattribute earlier measurements.

## Advanced audience targeting

Platinum and Diamond accounts can restrict delivery by reader experience, programming languages
and developer tools. Account audience defaults apply to its ordinary and inherited campaign
placements. Dated campaigns inherit these defaults when their audience is null; an explicit
empty audience means all readers. Product inventory filters and audience criteria are separate:
inventory filters select products, while audience criteria select eligible readers.

Experience choices are Aspiring developer, Entry level, Mid level and Senior. Language choices
must be active canonical language topics. Tool choices use active tool, platform, framework,
library, database, runtime, operating system, software, service, product, package, program or
technology topics. A reader must match any selected value within each populated dimension and
every populated dimension overall. Empty criteria add no audience restriction.

Populated criteria require a signed-in reader. Experience uses only the reader's optional private
profile choice; it is never inferred from their biography or dates. Languages and tools use their
current primary, hobby and learning stack, followed topics, and fresh valid inferred interests.
Past stack entries and expired, invalidated or stale interest generations are ignored. Experience
is not included in public profiles or Dev Cards. Placement requests never accept audience claims.

Audience checks apply equally to contextual delivery and the Sponsored main-feed fallback.
Article/topic relevance, account exclusions and campaign budgets still apply. An explicit active
campaign continues to own its matching product inventory even when a reader does not match its
audience, so inherited delivery cannot bypass its restrictions. Tracking receipts and counters
retain no reader identity or audience attributes.

Downgrading below Platinum preserves the saved criteria but disables targeted delivery and
invalidates outstanding targeted receipts. It never silently broadens the audience. Administrators
and account members may explicitly clear criteria; lower tiers cannot add or change populated
criteria. Retired or reclassified selected topics also prevent future targeted delivery. The
bounded audience-topic lookup resolves an account's saved unavailable selections for review,
while new choices and saves require current active topics of the appropriate kind.

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

Topic pages use prepared product/topic relevance. Main feed and search grids send at most 24
visible article IDs. The server resolves their current approved primary/supporting topics and
matches fresh prepared product topics; clients cannot supply topic claims or private articles.
Signed-in feed interests can strengthen those topics without introducing unrelated products.
Anonymous requests use the same visible context and never manufacture an interest profile.
Anonymous feed requests without context return no products unless the main feed explicitly enables
`allow_sponsored_fallback`. This permits an inherited Sponsored Feed or Homepage sponsorship placement when no eligible contextual placement of that format can be delivered. Engagement and sponsored articles always require relevance. Search, category and filtered feeds keep fallback
disabled; organic slots and article previews always require relevance. Source-filtered, saved, trending,
cursor and compact table views suppress product cards. News and opinion previews receive no
tutorial placements.

Requests read a bounded prepared candidate set and recheck current product, listing, account,
campaign and exclusion state. Cached matches expire after 30 minutes and are fenced by product
revision, product update time, article text/editorial revision and topic update time. New articles
are considered in each product slice; durable cursors also revisit older articles. Completed
sweeps become eligible again after five minutes. Preparation throughput depends on catalog and
article volume; missing, stale or unrelated matches result in no placement.

Each response contains at most two placements total, with at most one paid placement of each requested format. A feed-only request can include one non-campaign recommendation and one campaign-backed placement. Product destinations use HTTPS; sponsored articles use an internal article preview URL. Both display **Sponsored** in the reader. Matching reasons remain in the response for diagnostics;
they are not displayed in the card. Canonical products are deduplicated
across partners and slots. A funded winning product uses sponsored attribution; the ordinary slot
can show the next best eligible product. The explicit main-feed Sponsored fallback does not claim
an article or topic match and never fills the ordinary recommendation slot.

## Reader presentation and product images

Products use the article card's image area, spacing, body and footer inside the initial article grid, after the fourth and twelfth
articles when enough articles exist. Short grids have one cell after the available articles;
that cell prefers the eligible sponsored result when present. Articles retain their order,
keyboard focus and viewport position when product responses arrive or cards are hidden. Infinite
scroll does not add more product slots. Opening a preview suspends background delivery and
restores the same grid cards afterward, without refreshing the reading session.

Article previews show compact cards at the top of the sidebar, outside the article text. On
narrow screens that sidebar follows the article copy. Cards show a logo, name, bounded description
and destination. Every product card shows **Sponsored**, has no dismissal control and uses
`rel="sponsored noopener noreferrer"` on its destination link. There is no promotional banner,
automatic popup, matching explanation or ordinary-recommendation label.

The scheduler queues bounded logo discovery for qualified, active products through the durable
Images queue. Workers inspect official-site icon declarations (with the standard favicon as a
fallback), use the existing public-DNS/redirect/size guards, normalize supported images and store
immutable 32/64/96-pixel variants. Reader requests perform no remote icon discovery and expose
only managed assets. Missing or failed logos use product initials. Product URL changes fence old
logos until preparation completes; failed or unavailable icons do not block a relevant card.

Newly qualified or commercially accepted products also enqueue featured-image preparation.
Workers discover official-page Open Graph, Twitter or structured-data images with the existing
article fetch guards, store the original through the managed article-image pipeline and prepare
immutable 320/640/960-pixel variants. Grid cards use this featured image, with the product logo
centered in the same image area when no image is available or loading fails, then initials.
Reader requests expose only managed assets and never fetch remote product pages. Product URL
changes fence old images and queued work; a missing image completes once without repeated discovery.

Existing qualified or commercially accepted inventory can be prepared explicitly with
`devfeed_core.partner_product_images.backfill_product_images` in bounded, idempotent batches.
This backfill is for the local development catalog; deployment does not automatically backfill
existing production products.
The admin Images queue identifies each product and its logo or featured-image operation, links
to the product and retains independent retry histories for those two operations.

## Click destination

Administrators and current partner-account members can choose **Product website** or **Partner
listing**. Both destinations include `utm_source=devfeed`, preserving existing query parameters
and fragments. Partner listing delivery resolves an active, enabled, identity-resolved listing
owned by that account, ordered deterministically; another provider's canonical metadata listing
can never choose the destination. A missing or unsafe configured listing omits the card, with no
silent redirection to another partner or website. Portal preference updates can change click
destination and entitled audience settings independently, never tiers, trust or campaigns.

Admin account settings and the partner portal preview the destination using the account's first
qualified product, or a trusted accepted product when none is qualified. The preview shows only
the final URL and updates as the selection changes before saving. Accounts with no accepted
products show an empty preview. Previewing does
not reserve campaign delivery or record impressions and clicks.

## Delivery and reporting

`POST /v1/partner-placements/query` accepts a surface, its article/topic context or up to 24
visible feed article IDs, and up to 100 excluded product IDs. `POST /v1/user/partner-placements/query` additionally uses the
authenticated browser reader's interests and requires its normal origin/CSRF checks. Clients
cannot supply another user's identity. Both return `Cache-Control: no-store` and fresh signed
delivery receipts. The shared web/Chrome/Edge component requests and tracks placements separately
from streaming article/feed content.

The API's `sponsored` boolean denotes campaign backing for slot allocation, campaign budgets and
reporting. A non-campaign placement still returns `sponsored: false` and `campaign_id: null`.
Clients must not use this flag to determine disclosure, link attribution or dismissal controls:
every displayed product placement is labelled **Sponsored** and cannot be dismissed.

Products have no per-reader exposure limit or repeat cooldown. The reader avoids duplicate
products within a page and ignores earlier product hiding preferences, including values under
`devfeed:partner-placement-preferences:v1`. Tracking still counts a viewable impression when at
least half the card is visible for one continuous second while the document is visible.

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

Apply migrations through `0034` with `uv run devfeed db upgrade`. It follows the source-logo `0023` and
partner migrations `0024`–`0027`, preserves existing catalog, accounts, memberships and metrics,
and adds defaults, campaigns, exclusions, prepared matches and delivery reservations. Accounts
inherit their tier's promotion type and automatic inclusion; existing connections remain assigned
as before. Migration `0029` adds managed product logos and product subjects to the Images queue.
Migration `0030` adds managed featured images and independent logo/image jobs for a product.
Migration `0031` adds inherited commercial policy, scoped trusted acceptance, click destination
preferences and preservation of existing active dated agreements. Existing account tiers are
preserved; Silver and higher accounts can inherit Sponsored delivery. Review account policy before
enabling delivery.

Migration `0032` removes the obsolete per-reader exposure setting and its stored values.
Campaign budgets, placement attribution and measurements are preserved. Downgrading recreates
the older exposure setting with its original default of three; removed values cannot be recovered.
Migration `0033` adds optional account and campaign audiences, without changing existing delivery
eligibility. Downgrade pauses populated targeted accounts/campaigns before removing their criteria,
so an older runtime cannot broaden those audiences.
Migration `0034` adds native format identity, permanent article sponsorship, delivery locations and
nullable product allowlists. Existing accounts retain Feed as their saved automatic channel and
explicit type overrides are preserved; Silver now inherits paid promotion when no override is set.
Attribution is preserved. Downgrade pauses
inventory with unsupported channels or product restrictions, and unpublishes sponsored articles
before removing their disclosure field; retained article text and tracking history are preserved.

Set the same server-only `DEVFEED_PARTNER_TRACKING_KEY` in the public API and user API, using at
least 32 random ASCII characters. Compose injects it only into those two trusted backends.
The worker needs no signing key. An empty key disables delivery and collection. The scheduler
must run to prepare matches and automatic associations, and Redis must be available for query
and event rate limits. Frontends and extensions never receive the signing key.

Deploy all services requiring schema `0034` together after the migration. Downgrading removes new
campaign/default/matching/reservation data and automatic campaign associations; retain a database
backup before rollback. It preserves the original manually managed asset/metric data. Downgrading
only `0029` removes product logo metadata/jobs while preserving products, campaigns and reporting.
Downgrading `0030` removes featured-image metadata/jobs while retaining logos. Downgrading `0031`
removes commercial policy and pauses inherited agreements while retaining dated campaigns and metrics.

Validation includes disposable PostgreSQL/Redis tests for policy, concurrent caps, stale matches,
tenant boundaries and migration preservation. Run `npm run admin:test`, `npm run web:test`,
`npm run extension:test`, and the production browser checks
`node apps/admin/tests/browser/partner-accounts.mjs` and
`node apps/web/tests/browser/partner-placements.mjs` after building their apps and both extensions.
The placement and tracking browser checks also run in reader parity CI. Screenshots remain
untracked under `reports/partner-placements/`.
