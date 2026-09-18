# Changelog

Application releases and Alembic schema revisions are separate identifiers.

## 0.0.29 — 2026-09-19

- Show a recoverable article dialog for temporary upstream failures, with matching retry controls on web, Chrome and Edge.
- Add missing user/admin API request metrics, embed frontend release labels, and retain sanitized admin route templates in Faro.
- Index analysis candidate terms to avoid scanning the entire article separately for every catalog entry, preserving scores and publication safeguards.
- Include progressively loaded onboarding topics, explicit article-kind approval, and recent-entry source admission checks merged since 0.0.28.
- Prepare Chrome and Edge extension packages as version `0.1.9`; store publication is separate.
- Delegate Python connection reuse to PgBouncer with NullPool, bound HTTP concurrency separately, release API sessions before transmission and retain acquisition/hold-time metrics.
- Save one or more preferred article languages per account, defaulting to English; apply them before feed and source/topic discovery pagination and remove the single-language filter.
- Add source/content filters and sorting to My feed and Latest, including Most liked, while preserving choices during infinite scrolling and recovery.
- Replace remaining source pagination buttons with infinite scrolling and load catalogs incrementally across reader settings and onboarding.
- Compare PR performance against the base revision on separate runners after application tests, alongside image builds; retain reports and treat inconclusive comparisons as non-blocking.

No schema migration is required beyond the existing `0016` revision. Coordinate the release with the GitOps PgBouncer connection policy and telemetry delivery changes. Existing topic-proposal pauses and the 30-day protected history-retention policy remain unchanged.

## 0.0.25 — 2026-09-17

- Keep reader sessions active for 30 days of inactivity, up to a 90-day absolute lifetime; renew when returning to web, Chrome or Edge tabs.
- Keep scheduler heartbeat and quota checks responsive during long dispatch cycles, with bounded stall detection.
- Reduce sitemap and admin publication-query work; avoid unnecessary personalized-feed refreshes for inactive readers.
- Improve source relevance evidence validation and reuse shared topic catalog reads during analysis.
- Offer an explicit, temporary solver retry for source add/edit browser challenges without changing approval policy.
- Accept RSS/Atom article links with spaces in their paths, including Apache Uniffle's Atom feed; retain URL safety checks.
- Follow legacy publisher redirects over HTTPS only and improve image failure diagnostics.
- Add optional Compose registry proxying, including Docker Hub library and GHCR path handling.
- Cover every source intake with regression checks requiring an explicit AI or operator approval.
- Keep articles pending when analysis cannot establish eligibility.
- Keep the login cookie bounded by the absolute session deadline and renew only server-side inactivity expiry; validate cookie tokens at the response boundary and compare complete registry components in Compose checks.
- Release Chrome and Edge extension packages as version `0.1.5`.

No database migration is required. Existing reader sessions require one fresh login when these changes are deployed.

## 0.0.24 — 2026-09-16

- Preserve standard UTM attribution through anonymous reader redirects across web, Chrome and Edge, and correctly identify Edge extension analytics.
- Remove the unused admin knowledge graph explorer, its APIs and visualization dependencies; retain topic relationships and recommendations.
- Diversify Latest feed publishers with bounded, stable batches and cover both feed modes in query-budget tests.
- Apply search filters immediately with loading feedback and theme the extension install buttons.
- Deduplicate submitted sources by resolved feed URL and automatically reject sources with verified unrelated content.
- Improve database concurrency and bound analysis history.
- Release Chrome and Edge extension packages as version `0.1.4`.

Apply migration `0014` before replacing application services. It adds nullable history-retention markers and concurrent discovery/retention indexes; it does not immediately prune history or alter publication decisions. Inspect invalid indexes before retrying an interrupted migration. Older services require schema `0013`, so readiness can briefly drop during migration and replacement; recover with schema-compatible images. Browser-store publication is separate from website deployment.

## 0.0.23 — 2026-09-16

- Allow source approval when at least 80% of sampled entries are relevant, even if the remaining entries are uncertain; retain confidence and grounded-evidence requirements.
- Research computing concepts and disciplines as valid topic identities instead of requiring a uniquely named product or organization.

No schema migration is required. Existing deferred source and topic reviews retain their state and require targeted reprocessing after deployment.

## 0.0.22 — 2026-09-16

- Let new users choose at least three topics in a simple feed onboarding modal, ordered by article count with search.
- Restore topic follow controls and preserve filtered topic destinations through guest sign-in on the website and both extensions.
- Precompute hourly shuffled personal feeds in Redis with stable pagination and an unshuffled database fallback.
- Coordinate concurrent cold sitemap requests to avoid transient fetch failures.
- Store original article images and responsive WebP thumbnails in R2 through the existing Images pipeline, with resumable backfill and a dedicated internal imgproxy service.
- Release Chrome and Edge extension packages as version `0.1.3`.

Apply migrations `0012` and `0013` before replacing application services. Configure production R2 storage and imgproxy signing credentials for the Images worker; expose the public image domain to reader APIs. Existing articles retain their original images until backfilled. Older services reject schema `0013`, so readiness can briefly drop during migration and replacement; recover with schema-compatible images. Browser-store publication is separate from website deployment.

## 0.0.21 — 2026-09-15

- Preserve personal-feed cards through extension refreshes and session rotation; put My feed first across web, Chrome and Edge.
- Offer first-time website visitors the published Chrome and Edge extensions without showing the invitation inside extensions.
- Remove obsolete personal-feed routes and root-filter compatibility redirects.
- Review vague and sensational article headlines with source evidence while preserving publisher titles; block utility pages from publication.
- Enforce reader browser parity in CI and extension validation in pre-commit checks.
- Release Chrome and Edge extension packages as version `0.1.1`.

Migration `0011` adds the nullable editorial article title. Existing content is preserved and is not automatically reanalyzed. Older APIs require schema `0010`, so readiness can briefly drop between migration and replacement. Roll forward with schema-compatible images if recovery is necessary. Store submission and approval are separate from website deployment.

## 0.0.20 — 2026-09-15

- Show live account quota usage and reset times in the AI connection popover instead of a single model name.
- Make My feed the signed-in homepage, move public browsing to `/latest`, and hide saved-article navigation when signed out.
- Keep the previous recommendation generation visible while background work prepares updates.
- Pace AI admission against the shared account weekly quota with a reserve, instead of a fixed relationship call limit in production.
- Remove the overview header's refresh button, progress text, and panel-attention summary while retaining automatic updates and per-panel error recovery.

No database migration is required; schema `0010` remains current. Production configuration increases dedicated article-analysis workers to three and enables shared weekly-quota pacing with a 20% reserve, without pausing relationships behind topic work. Provider cooldowns remain enabled.

## 0.0.19 — 2026-09-15

- Import OPML, JSON, Markdown, and URL collections through the admin interface or CLI, retaining provenance and ignoring duplicates.
- Discover and validate publisher feeds, preferring valid Atom feeds with RSS fallback. Imports remain pending until reviewed; full-auto mode uses AI review, and approval enables polling.
- Show imported and manually added sources in the same table, forms, selection, and deletion controls.
- Recover durable discovery work, expose progress, respect robots rules, and prevent duplicate rejection or obsolete enrichment results from changing the wrong source.
- Organize the admin overview into clear sections, simplify refresh timestamps, and show current queued/running work in a compact live donut and count table.
- Keep the sidebar visible during scrolling, make audience details directly visible, and remove the search-result hover background.
- Upgrade Protego to address its wildcard-matching denial-of-service vulnerability.

Apply database migration `0010` before replacing application services. Existing accounts, articles, source decisions, and preferences are retained. Older application readiness rejects the new schema, so a brief interruption is possible during rollout. Keep schema `0010` and use a compatible forward fix if recovery is needed.

Discovery/RSS challenge-solver support is not included. Sparse or uncertain source assessments remain pending for manual review. Browser-store publication is separate from this application release.

## 0.0.18 — 2026-09-14

- Add DevFeed new-tab extensions for Chrome and Microsoft Edge with the shared
  reader, account features, local routing, and optional separate analytics.
- Fix personal-feed recovery and keyboard skip navigation in extension tabs.
- Produce separate store ZIPs without the development manifest key.
- Cover both browser extensions and their DevFeed name in the privacy policy.

Rollout: update web and user API together. Configure exact extension IDs on both
services for account writes. Analytics remains separately opt-in on the server.
No database migration is required. Store publication is a separate step.

## 0.0.13 — 2026-09-14

- Fix sign-in from search and topic pages. Preserve search queries and filters through
  the login callback while retaining strict local return-destination validation.

## 0.0.12 — 2026-09-14

- Notify source submitters when their suggestions are approved or rejected, and
  recover queued notification and recommendation deliveries missing from Redis queues.
- Isolate admin reporting database connections and bound Overview refreshes with
  cached snapshots, timeouts and retry backoff.
- Let article analysis select exact evidence passages, avoiding quote-copy failures
  without weakening the evidence validation gate.
- Add follow controls to search topics and sources. Improve chart tooltips, show
  every task label, and format large token totals for readability.
- Fetch topic evidence concurrently with stable citation IDs and reuse public evidence
  for at most five minutes without renewing its source-validation timestamp.
- Preserve valid drafts when retrying malformed independent verification. Keep all
  approval, scope, evidence, revision and whole-workflow budget gates.
- Admit up to two jobs per observed available topic worker, bounded by an eight-job
  default ceiling; pause new admission during provider cooldowns. Reserve FIFO work
  while prioritizing eligible pending articles' exact tag matches.
- Add verified-decision/publication throughput charts, recorded processing times,
  queue age, observed worker availability and the effective topic admission window.

Rollout: raise the production `DEVFEED_TOPIC_DECISION_MAX_PENDING` override
from `4` to `8` with the new scheduler. Retain current replicas initially; three
healthy dedicated topic workers yield a six-job admission window. Reassess using
completion rates and worker utilization after rollout before increasing replicas.

- Expand the default AI content window to source publication dates on or after
  July 1, 2026, inclusive at midnight UTC. Topic research remains enabled;
  undated and older content remains deferred.

Rollout: change the production GitOps ConfigMap's explicit
`DEVFEED_AI_CONTENT_NOT_BEFORE` override from `2026-09-01` to `2026-07-01`
when deploying this release. Updating application defaults alone does not override
that setting. Apply it consistently to APIs, scheduler and workers; normal scheduling
can resume eligible pending articles without resetting existing deferred jobs.

Schema remains `0009`; the automatic pre-upgrade migration hook is idempotent.

## 0.0.11 — 2026-09-14

- Reuse an existing topic website before spending on model discovery. If a lookup
  is needed, request one search for URLs and fetch the pages only in the backend.
- Fit live web-tool overhead within a 40,000-token per-call guard while retaining
  the 64,000-token whole-topic budget and independent identity/scope verification.
- Let explicit budget grants adopt the current per-call guard with an audit trail.
  Keep per-job usage separate from the complete topic ledger across grants.
- Constrain evidence selectors to the saved excerpt IDs in the output schema and
  keep provenance hashes out of model prompts, preventing avoidable copy errors.

Schema remains `0009`; the pre-upgrade hook is idempotent. This corrects discovery
overhead observed during the v0.0.10 production canary. Deferred reviews retain
their spent budget and require an explicit grant or manual review.

## 0.0.10 — 2026-09-13

- Add task-based Luna routing with validation-triggered Terra escalation, explicit
  reasoning effort, and durable whole-topic processing budgets.
- Reuse primary-source evidence for minimal topic drafts and independent review;
  select exact excerpt IDs, defer unresolved reviews, and retain audited budget grants.
- Prioritize topic decisions over optional relationship expansion and cap daily
  relationship inference calls.
- Add Overview charts for per-call task/model usage, cache and reasoning tokens,
  searches, duration, outcomes, topic backlog, decisions, repeat work and escalations.
- Add repeatable whole-workflow benchmarks with frozen or live evidence, domain
  summaries, measured costs, and independent output-bound review gates.
- Correct inherited MCP configuration handling for isolated local model tests.
- Accept short, relevant article summaries without automatically rejecting the article.

The pre-upgrade hook must apply schema `0009` before new pods start. Existing
v0.0.9 APIs require `0008`, so expect a brief readiness interruption during the
schema transition. Budget/routing switches remain explicit deployment settings.
See [topic decision operations](docs/topic-decision-operations.md) for limits,
review requirements and recovery without silently resetting consumed capacity.

## 0.0.9 — 2026-09-13

- Save articles to a private read-later list and share them through copy link,
  Reddit, X and LinkedIn. Open original articles directly from cards in a new
  tab while recording views.
- Improve article modal layout, source and topic follow controls, and accessible
  reader transitions.
- Add infinite search scrolling, article thumbnails, content-type pills, filters,
  sorting and shared calendar controls that prevent future date selections.
- Default new sources to twelve-hour polling and provide a repeatable local
  development seed of published content.
- Record per-call inference tokens and costs, reduce unnecessary inference work,
  and use structured analysis fields for publication eligibility.
- Add repeatable, reviewable model benchmarks with bounded Gemini and OpenRouter
  requests. Benchmark candidates do not change production model routing.

The pre-upgrade Helm hook must apply schema revisions `0007` (bookmarks) and
`0008` (inference accounting) before new application pods start. Both migrations
add tables without rewriting existing content. Older APIs enforce their schema
revision, so allow for a brief readiness interruption during the upgrade. Keep
schema `0008` when rolling forward; an image-only rollback to `0.0.8` is not
supported by its schema readiness check.

## 0.0.8 — 2026-09-13

- Default content-based AI processing to source publication dates on or after
  September 1, 2026. Defer older and undated content without inference or editorial
  changes, retain topic/relationship research, and allow later resumption by
  changing or clearing `DEVFEED_AI_CONTENT_NOT_BEFORE`.
- Add private metrics for APIs, frontends, worker queues, database access, and
  product freshness, with bounded read-only background snapshots.
- Add sanitized distributed traces, Python and Node profiling, structured logs,
  and anonymous browser telemetry with private source maps.
- Bound production metadata queries and add twelve concurrent indexes in schema
  revision `0006`. APIs accept both `0005` and `0006` during the upgrade.
- Preserve browser trace identity across internal API calls and reject public
  metrics requests before frontend streaming starts.

Roll out the new APIs while retaining schema `0005`, retire old API replicas,
then apply migration `0006`. Keep telemetry endpoints private and choose worker
capacity from measured queue demand and drain time before production deployment.
See [observability operations](docs/observability.md) and
[worker queue operations](docs/worker-queues.md) for staged rollout and rollback.

## 0.0.7 — 2026-09-13

- Split article analysis, topic research, research verification, source analysis,
  relationships and enrichment into independently scalable worker queues.
  Preserve legacy deliveries, readiness checks, retries and exclusive job claims.
- Expose every queue and its worker capacity in admin monitoring and CLI status;
  add dedicated Compose services and a staged queue migration guide.
- Reduce admin dashboard database contention by coalescing refreshes, releasing
  connections earlier and bounding expensive JSON extraction to recent jobs.
- Reduce inference retries with catalog-constrained IDs and safe validation feedback;
  avoid unnecessary article reanalysis when unused fallback topics change.
- Keep dispatch and lease recovery metadata-only and add partial indexes for
  running-job lease scans in Alembic revision `0005`.

Deploy migration `0005` before starting this release. Upgrade mixed consumers
before producers and retain an updated `analysis` group until legacy deliveries
drain. No content or preferences are deleted. See [worker queue operations](docs/worker-queues.md).

## 0.0.6 — 2026-09-13

- Add public Terms of Service and Privacy Policy at `/legal/terms` and
  `/legal/privacy`, with navigation links, contact details and sitemap entries.
- License the project under MIT and document private vulnerability reporting in
  `SECURITY.md`. Add a contributor guide covering setup, checks and pull requests.
- Search articles, topics, sources and tags together on a dedicated search page,
  with article-first results, typo tolerance and bounded response times. Typesense
  indexing uses a durable PostgreSQL change queue and a dedicated indexer.
- Add shared infinite scrolling across reader lists, hide sources without published
  articles and introduce stable source slugs with legacy URL redirects.
- Publish cached sitemap indexes and article, topic, tag and source sitemaps with
  stable URLs, canonical links, publication dates and priorities. Add robots.txt,
  llms.txt, llms-full.txt and Markdown representations through Dualmark.
- Add the new social-preview artwork, Open Graph and Twitter cards, and JSON-LD
  structured data for public pages. Keep private and search pages out of indexing,
  and disable local analytics through an explicit runtime switch.
- Replace the oversized source chart with a compact publication donut and add
  reader-engagement charts. Improve source headers, logo contrast, content-type
  badge colors, worker names, common table cells and compact token tooltips.
- Show complete truncated article titles in accessible tooltips and preserve
  anonymous original-article click tracking with abuse limits after session expiry.
- Route browser-challenged source, article and image enrichment to a dedicated
  solver queue, with configurable solver services and bounded concurrency.
- Allow source deletion when articles or jobs reference it, repair sign-in return
  paths, preserve manual topic exclusions and improve durable queue ordering.
- Bound database connection failures and pool waits, verify recovery after pooler
  failure, move blocking API work off the event loop and complete backend request
  and response contracts.
- Standardize Python and TypeScript formatting, add scoped pre-commit checks and
  repair CI integration-test and Compose validation coverage.

Deployment requires migrations `0002` through `0004` for solver routing, the search
outbox and source slugs. Provision Typesense and its separate query/indexing keys,
run the search indexer, and enable the dedicated solver worker. Existing accounts,
content and user preferences are preserved. Automatic GitHub avatar import is not
included in this release.

## 0.0.5 — 2026-09-12

- Support Sentinel-managed Redis and Valkey across APIs, sessions, queues, caches,
  rate limits, AI cooldowns and runtime logs, with separate discovery credentials.
- Reconnect to the elected writable primary after failover while retaining the
  configured logical database, data credentials and TLS policy.
- Allow workers to release idle database connections to an external session
  pooler instead of retaining a local pool per process.

No database schema changes.

## 0.0.4 — 2026-09-12

- Load job source and article names with the job page, removing per-row browser
  requests while retaining the existing SQL query budget.
- Release database connections before job-list and source-detail response
  validation, preventing connection-pool contention during concurrent reads.

No database schema changes.

## 0.0.3 — 2026-09-12

- Replace the source-output scatter plot with ranked publication bars, visible
  source names and exact counts. Show fetch failures separately.
- Format AI token summaries and chart labels with compact lowercase units such as
  `100k` and `61.4m`, retaining exact counts in tooltips.
- Explain publisher browser challenges during feed validation instead of showing
  an unexpected HTTP 202 error.

No database schema changes.

## 0.0.2 — 2026-09-12

- Pause automatic polling, notification streams and infinite scrolling when a
  browser tab is hidden or unfocused. Resume on return while preserving displayed
  data and the saved refresh interval, including Off.
- Show daily AI token usage by job type on the admin overview, including article
  analysis, topic analysis and research verification.
- Accept empty GitHub topic aliases, retain the first 50 aliases when imports
  exceed the limit, tolerate duplicate unused metadata and explain import failures.
- Normalize topic descriptions to plain text throughout the APIs, reader app and
  administration, including older stored descriptions.
- Refresh the product README with branding, website links and workflow guidance.
- Adopt centrally maintained CI workflows for ARM64 tests, security checks and
  verified publication of all six runtime images.

No database schema changes. Existing accounts, content, preferences and AI sign-in
state are preserved.

## 0.0.1 — 2026-09-12

First release of DevFeed, a developer news aggregation and discovery platform.

- Public reader app with article slugs, search, content filters, topic/source browsing,
  Markdown, article previews, keyboard navigation, themes and loading states.
- Independent OIDC user and admin sign-in with PKCE, organization checks, secure
  server sessions, CSRF protection and role-gated administration.
- User preferences, topic/source follows, likes, personalized recommendations and
  authenticated source suggestions with feed validation and editable feed names.
- Isolated administration app with operational overview, publishing and taxonomy
  workflows, source review, user analysis, job history and bounded runtime logs.
- Durable RQ ingestion and enrichment, original-article click tracking with shared
  abuse limits, public-response filtering, and Redis response caching.
- Full automation for AI analysis, topic research, classification and evidence-based
  source approval. Uncertain or irrelevant source suggestions remain unapproved.
- Separate notification environments and credentials for reader and admin inboxes.
- Deferred GA4 integration and typed events for original-article clicks, likes,
  follows, suggestions and saved preferences, without account identifiers or free text.
- One initial database migration (`0001`), operator CLI and six independently built,
  scanned and tested ARM64 runtime images, including isolated Codex.
- Authenticated TLS transport for remote AI workers and configurable database pool
  limits for shared PostgreSQL deployments.
- Updated JavaScript/Python dependencies, Node 24 LTS images and Codex 0.154.0.
  ESLint remains on its latest compatible 9.x release because the React plugin does
  not support ESLint 10; Pydantic controls its required `pydantic-core` version.
