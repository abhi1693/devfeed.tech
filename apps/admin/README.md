# DevFeed Admin

Independent Next.js administration UI, using the admin API through same-origin
App Router API routes. It has no direct database, Redis or OIDC-provider access.
The UI sends HSTS and a per-request nonce-based script policy; the internal
Traefik ingress also redirects HTTP to HTTPS.

See [configuration, authentication, service boundaries and commands](../../docs/admin.md).

## User details

The user Details tab shows reader-profile fields and Dev Card styling only when
the account has a configured username. Claimed profiles remain inspectable by
admins when public visibility is disabled. Accounts without a username still
show account information, reading activity, personalization, and preferences.

Validate these states with `npm run admin:test` and, after `npm run admin:build`,
`node apps/admin/tests/browser/user-must-reads.mjs`.

## Shared table columns

`DataTableColumn` and resource `ColumnSpec` accept a `kind` that selects a shared
`ColumnValue` renderer. Use `name` for names, `slug` for literal monospace slugs,
`boolean` for accessible yes/no indicators, `tags` for lists of badges, `pill` for
colored statuses, and `datetime` for dates using the operator's display settings.
`text`, `number`, `language`, and `label` cover plain values and explicit enum labels.

```tsx
const columns: DataTableColumn<Row>[] = [
  { accessorKey: "name", header: "Name", kind: "name" },
  { accessorKey: "slug", header: "Slug", kind: "slug" },
  { accessorKey: "enabled", header: "Enabled", kind: "boolean" },
  { accessorKey: "tags", header: "Tags", kind: "tags" },
  { accessorKey: "status", header: "Status", kind: "pill" },
  { accessorKey: "created_at", header: "Created", kind: "datetime" },
];
```

Pills infer their semantic color from known statuses; set `tone` to `success`,
`warning`, `info`, `danger`, or `neutral` for other values. Tags accept strings or
objects with a `name`. Missing values share an em dash; false and zero remain
visible. Names and slugs preserve their spelling. A custom `cell` takes precedence
for links, actions, and other specialized content. Individual cell components
are also exported from `components/molecules/column-value.tsx`.

## Source relevance assessments

The **Relevance** tab immediately after **Details** shows the saved assessment separately
from the source's actual approval status. Its decision summary and evidence sit side by
side on desktop. Evidence is paginated in groups of five and can be filtered by classification. The panel explains sample-size, confidence, and classification-count blockers;
uncertain entries count toward the sample total but do not support rejection. Article
titles expand to show their matching feed summary and quoted evidence. Model, version,
and assessment time are under **Assessment details**.

The backend's saved approval/rejection flags are authoritative. The UI explains known
policy versions; it never makes a review decision. Keep its explanatory thresholds in
sync with `packages/core/src/source_relevance.py` when introducing a new policy version.

Validate the panel with `npm run admin:test` and, after `npm run admin:build`,
`node apps/admin/tests/browser/source-relevance.mjs`. The browser check uses a disposable
API fixture and saves desktop/mobile screenshots to `reports/source-relevance/`.

Source policy v4 includes software product management and growth, practical AI product
building, engineering leadership, and careers in software/product roles. Older saved
assessments display a notice that re-analysis is needed under this broader scope. This
source-admission policy is separate from the technical topic taxonomy. Evidence,
confidence, and sample-size requirements are unchanged.

Source policy v5 removes the 80% article requirement for approval: an overall in-scope
assessment with at least 90% confidence, at least three usable sampled entries, and at
least one validated in-scope quote can approve a mixed-content source. Individual
article review is unchanged. Automatic rejection retains its 80% evidence requirement.
The scheduler requeues pending assessments from v1–v4 once, excluding active or failed
jobs; current-version assessments are not repeatedly queued.

## Article eligibility

Classifying an article requires a page-kind decision before approval or publication.
The form preserves an existing decision and defaults missing/legacy decisions to
`uncertain`. Select `non_article` for RSS subscription/feed-link pages, signup forms,
About/contact pages and other site utilities. Technical vocabulary or matching
topics does not establish article eligibility. Actual RSS tutorials remain eligible.
Manual classification API/CLI clients should send `page_kind`; omission records
`uncertain` and blocks approval until the page has been assessed.

Validate the page-kind form with `npm run admin:build` followed by
`node apps/admin/tests/browser/article-page-kind.mjs`. The browser fixture checks
legacy defaults, saved decisions, and desktop/mobile layout.
Screenshots: [desktop](screenshots/article-page-kind-desktop.png),
[mobile](screenshots/article-page-kind-mobile.png).

## Launch platform partnerships

Open **Partnerships → Partners**, choose **Create partner**, and select a supported platform.
Use the Enabled setting to start or pause automatic syncing. Each partner has a details
page and a separate edit form; synced products appear under **Partnerships → Products**.
Products use the shared admin table with search, sorting, pagination, and column preferences.
Open a product for its description, assessment, capability evidence, and record information.
Its **Related objects** tab shows each platform's listing and attribution alongside pipeline
jobs and evaluations. Retry checks and exclusion remain available on the product detail page.
The partner detail page uses the shared record information panels and a **Related objects**
tab with paginated products, pipeline jobs, and evaluations. Open a run for its detail page.
**Partnerships → Pipeline jobs** lists discovery, individual product syncs, and qualification
runs with status, operation, search, sorting, and pagination. Runs link to their parent,
child jobs, partner, product, and dedicated logs page.
Discovery and product syncs use the `ingestion` queue consumed by the standard background
worker. They do not require the optional source-discovery worker. Qualification and
article matching use the AI workers.
**Partnerships → Evaluations** lists article-matching runs. Each run has Details, Related
objects, Results, and Logs tabs. Results show article decisions, reasoning, and source
quotes, and indicate when product or article changes made the results outdated.
The provider catalog comes from the API. A platform can be added once; edits use revision
checks to prevent overwriting another administrator’s settings.
DevFeed pulls developer products directly from the platform API. Set **Sync interval
(minutes)** when creating or editing each partner: 1–10,080 minutes, defaulting to 360
(six hours). Interval edits recalculate the next sync from the last successful sync; an
overdue schedule becomes due immediately. If no sync has succeeded, the interval starts
from the edit time. Active discovery and product jobs continue, and completion schedules
the next run using the latest interval. Disabled partners retain their interval without
scheduling work. **Sync now** refreshes early; disabling a partner in **Edit** stops synchronization
and invalidates its in-flight sync jobs. Shared product checks can continue through another enabled partner. No product creation, editing, JSON upload, manual approval,
or article-ID entry is available.

Connections identify both the partnership type (`launch_platform`) and provider
(`nick-launches`). This implementation covers launch platforms. Ad networks such as
Carbon Ads and EthicalAds, and direct paid partnerships, need their own integration and
commercial workflows; they are not catalog providers in this flow. Billing, ad delivery,
and reader placements are not enabled by connecting a launch platform.

The adapter follows cursor pagination, accepts the documented and live response envelopes,
normalizes the platform listing and product website URLs, and selects the Developer Tools
category. Discovery saves its page cursor and queues one durable job per product. Each
product job fetches that product's API endpoint and commits it separately; a failed product
cannot roll back its neighbors. Successful jobs are not rerun when another product retries.
The next scheduled sync or **Sync now** resumes a failed run once at its saved cursor,
retrying failed products without replaying successful ones. If that generation fails again,
the next sync starts a fresh API scan so corrected or removed upstream entries can recover.
Duplicate listings within a run share one product job.
Product changes trigger new checks. Missing products are withdrawn only after a
complete successful scan and successful product jobs; failures preserve existing listings. Withdrawal cleanup runs in batches of 25. Legacy products without an API connection remain hidden and ineligible.

Automatic checks fetch each product's public website using the shared SSRF-protected
fetcher. Qualification requires a concrete development use case, specific technologies,
and quotations found in the fetched page. Uncertain products remain unqualified; irrelevant
products are excluded from matching. Admins can retry checks or exclude a product without
editing source data. Exclusions survive subsequent syncs.
Retrying checks cancels any queued or running evaluation for the previous product revision;
successful requalification queues current matching results automatically.

Qualified products automatically receive private relevance evaluations against up to 15
published tutorials and five other articles as negative controls. Evaluations refresh when
the product changes or a later sync finds a different article sample. Only grounded task
matches qualify; generic category overlap is insufficient. Qualification expires after
90 days. Nothing is added to the reader feed, releases, search, MCP, or extensions.

Migration `0021` creates the launch platform connections, canonical products, listings, URL
aliases, per-product sync jobs, and evaluation jobs in their final form. Run the normal
database upgrade before starting the API and workers. Downgrading removes these partnership
tables and their data.

Development databases upgraded to the former PR head `0025` have the final table layout.
Verify that schema against the consolidated migration before stamping its revision as
`0021`; superseded revisions are no longer in the migration graph. Databases on earlier
intermediate PR revisions must first finish the old migration chain or be recreated if
their development data is disposable.
The scheduler dispatches discovery and product API syncs on `source-discovery` and product checks and
matching on `source-analysis`. AI must be enabled for checks; API syncing can continue
while AI is disabled. Jobs are leased, retried with backoff, and protected against stale
results. API connections are currently public and require no partner credentials.

Connection, pause, manual sync, exclusion, and recheck actions emit structured admin API
logs after the change is committed. Scheduler and worker logs identify the provider,
operation, job, parent run, external product ID, attempt, and page, with page counts, qualification decisions, retries,
failures, cancellations, and completion timings. Sync/qualification worker logs use
`partner-pipeline`; article matching uses `partner-evaluation`. Both can be read through
`GET /v1/admin/jobs/{kind}/{job_id}/logs` with the existing admin authentication and log
retention policy. Connection action logs include affected job IDs for correlation.

### Shared product identity

The catalog displays one product with all of its platform listings. Product capabilities,
qualification, article matches, and exclusions belong to that shared identity. Each listing
retains its provider ID, platform URL, supplied description, pricing, attribution, availability,
and last sync time. Additional listings cannot overwrite the product's display information;
its original listing remains the explicit metadata source. Official-site evidence determines
verified capabilities.

Identity matching normalizes full official product URLs and removes known tracking parameters.
Meaningful paths and query parameters are preserved; names and domains alone do not merge
products. Actual website redirects can verify HTTP-to-HTTPS and www aliases when the path and
query remain identical. Aliases are resolved before a redundant AI assessment, and valid
existing qualification is reused. Cross-domain or path-changing redirects do not establish
identity automatically. A listing whose website changes without verified identity remains
unresolved and cannot make its product eligible. Later syncs retry verification of its old
website and can resolve a matching safe redirect automatically.

A platform sync withdraws only that platform's listings. A product becomes unavailable only
when no active, resolved listings remain. Pausing one platform does not cancel shared work
when another connected platform still supplies the product. Global exclusions survive new
listings and identity merges. Concurrent syncs serialize short catalog transactions and use
unique URL identities, avoiding duplicate products and qualification jobs.

Only Nick Launches has an enabled API adapter today; future launch platform adapters share
this identity model. Ad network integrations and commercial agreements remain separate
future workflows and do not determine product identity or qualification.

## Next.js 16.4 tooling

The app uses Next.js 16.4 and React 19.3. The Rust React Compiler optimizes
component rendering with Turbopack. Cache garbage collection, lazy client dynamic
imports, and worker-thread plugin execution are enabled for development/builds.
Next.js may fall back to child processes on Node.js versions affected by its
worker-thread compatibility checks. Upgrade reminders use the `latest` policy.

Run `npm run admin:analyze` from the repository root for the interactive Turbopack
bundle analyzer, or `npm run admin:analyze -- --output` for an offline report under
`apps/admin/.next/`. Analysis does not replace a production build. Use the analyzer
to compare route sizes and identify large dependencies before making changes.
Save a named capture with `npm run admin:analyze -- --output --snapshot baseline`
and compare subsequent captures in the analyzer.

Cache Components and Partial Prefetching remain disabled: both apps use a fresh
script nonce per response, and Next.js static shells are incompatible with this
[CSP model](https://nextjs.org/docs/app/guides/content-security-policy).
Consequently, `ensureStatic`, `navigation()`, and `prefetch()` are not applied to
these request-rendered routes. Adopting them requires a compatible CSP design
and verification of runtime settings, authentication, and personalized feeds.

Validate with `npm run admin:lint`, `npm run admin:test`, and `npm run admin:build`.
The overview browser suite checks nonce rotation after its overview scenarios so
the extra page loads do not affect panel concurrency or lazy-loading measurements.
