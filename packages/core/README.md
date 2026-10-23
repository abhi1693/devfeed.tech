# Shared backend core

## Partial tag edits

`TagPatch` uses Pydantic's stable `MISSING` value for omitted fields. Ordinary
`model_dump()` and JSON serialization include only the supplied changes, so an
empty patch remains empty when replayed. Empty aliases and `auto_link_topic=false`
remain explicit updates. Only `topic_id` accepts `null`, which removes the topic
link and disables automatic matching unless the caller also explicitly enables
it. The OpenAPI schema and generated administration client preserve these rules.

## Source admission and recovery

`source_lifecycle` derives Discovery, Profile, Assessment, Branding, Review, Ingestion,
Active, Paused and Rejected from source decisions and existing durable jobs. Reading
the board does not schedule work or change already-approved polling. Batch fact
loading selects active work before terminal history; the same checks protect board
actions, manual approval and enabling a source.

First approval requires a successful profile captured for the current feed, source
type and website, plus settled branding. A completed profile without a logo, or
disabled image storage after active logo work settles, permits the normal fallback.
Manual review may override uncertain or failed relevance assessment. Automatic
decisions retain the existing grounded source-relevance policy and require current
assessment evidence; supported rejection need not wait for branding. Approval queues
ingestion once rather than starting a second profile request.

Profile fetching and AI relevance use separate `SourceEnrichmentJob` operations. A
relevance job uses the bounded feed sample saved by its successful profile and can
retry without downloading metadata again. An obsolete source identity cannot apply
results. Existing queued/running jobs retain their leases and coalesce requests.

An explicit upstream retry on an approved source pauses new ingestion and records a
private `processing_request` with required phases and operator attribution. That
intent survives job replacement and additional retries. It clears only after an
explicit successful Resume/enable or a new approval decision. Background completion
never resumes the source. An ingestion-only retry keeps the source enabled. Pausing
also fences an already-running fetch before article writes, without counting an
intentional pause as a feed failure. Existing articles and approval history remain.

Rejected sources may explicitly Reopen for review through the snapshot-guarded board
action. This records operator intent, returns the source to pending and disabled, and
requests fresh profile/discovery work without inventing an approval or ingestion.
Older in-flight work retains its lease but cannot replace that fresh inspection.
Reopened sources require manual approval even when full automation is enabled; prior
reviews and existing articles remain available.

Migration `0032_source_processing` defaults historical job operations to `legacy`
and preserves source approval/enablement. Unbound historical profile evidence may
support existing approved polling, but cannot prove first admission for a pending
source. The private intent is not an independently persisted workflow stage. It follows schema `0031`; deploy migration `0032` before starting the new API
and workers because older schemas lack the required processing fields.

## Original article attribution

`ArticleOut` keeps DevFeed's display title separate from `original_title`. A publisher
headline is exposed only when its provenance is known. `publisher` resolves a single
approved publisher origin on the original article's website, independently of the
alphabetical discovery-source order. Ambiguous identities remain unknown. `authors`
contains only explicitly typed people or organizations; current retained bylines lack
that evidence, so they remain in the existing `author` display field without a guessed
JSON-LD author type. The additive fields require no database migration.

Reader pages retain their DevFeed canonical URLs. Their `WebPage` identifies DevFeed
as the page publisher while describing the external original `Article` separately.
A visible AI overview is a local English `CreativeWork` linked with `hasPart` and
`isBasedOn`. Its text preserves the overview's Markdown source, declared as
`text/markdown`, including technical identifiers and code. Source excerpts are not
claimed as DevFeed-created work. Original dates
and bylines are not reused as the overview's own metadata. Source breadcrumbs and
reader source links prefer the verified publisher, with discovery sources retained
as a navigation fallback for older responses and unknown publishers. This changes
neither indexing eligibility nor sitemap membership based on overview length.

## Backend service boundaries

- `devfeed_core.partner_connectors.ConnectorClient` binds one provider, validated connector
  definition, credential and request deadline. Discovery, detail mapping and response sampling
  share that context and the same origin, redaction and request-budget checks. Workers construct
  clients from their saved job definitions rather than reading mutable connection settings.
- `devfeed_http.partner_connector_previews.ConnectorPreviewService` shares the bounded preview
  workflow between the admin and partner APIs. Each API still authorizes the request, resolves
  permitted credentials and releases its database transaction before remote I/O.
- `devfeed_http.partner_campaigns.CampaignService` owns account-scoped campaign queries,
  validation, disclosure and enrollment. Routes authorize access, lock the account for writes
  and commit the transaction; the service only flushes, so sponsorship disclosure and enrollment
  remain atomic with the campaign save.
- `devfeed_http.body_limits.BodyLimitedRoute` enforces the registered endpoint's
  `RequestBodyPolicy` before parsing or dependencies. `BoundedRequestBody` owns byte counting,
  the total read deadline and replay; the policy supplies JSON or image-upload errors.
  Gateway-backed endpoints use `bounded_openapi_extra()` to derive backend limits from
  their existing transport policy, without maintaining another URL or method selector.
  Matched endpoints remain bounded when identifiers fail typed validation. Native path
  converters still return 404 for unmatched identifiers without reading a request body.
- `devfeed_http.inbox.InboxRouter` registers the 17 supported inbox operations for the
  admin and reader APIs. Each `InboxOperation` declares its upstream target, JSON or event-stream
  response and transport bounds. The API supplies a verified `InboxSession`; subscriber identity
  and HMAC headers stay server-side. `InboxProxy` reads bounded bodies after session authorization
  through the shared `RequestBodyReader`, and owns upstream cleanup through stream completion or
  disconnect. Generated gateways use these concrete operations instead of an inbox catch-all.
- `devfeed_api.cache.CachePolicy` makes public response caching opt in on each registered
  read through `x-devfeed-cache` metadata. The namespace and revision identify the response
  contract; path parameters and ordered query values identify its inputs. Its `content` or
  `metadata` TTL uses the existing cache settings, independently of the route's URL or mount.
- `devfeed_http.route_policies.RouteRuntimePolicy` declares admission lane, telemetry
  exclusion and successful-request logging level through `x-devfeed-runtime` metadata.
  `install_runtime_policies()` binds these to native selected routes after registration,
  including deferred router prefixes and mounts. Liveness is exempt; readiness retains
  request admission. Only the inbox event operation uses stream admission. Slots remain
  held through response and background cleanup, and undeclared operations use the request
  lane. The native telemetry bridge starts observation at the same selected boundary;
  framework redirects and CORS responses use the default policy without another router.
- `devfeed_partner_api.access.PartnerAccountAccess` binds a database session and verified
  identity. Portal, connection and workspace queries share its active-account and membership
  scope. Writes lock the account and the exact membership until commit, including preference
  saves. Product and catalog locks precede these authorization locks; rate limits, external
  requests and transaction commits stay with their existing callers.

## Placement serving

Active products queue qualification renewal seven days before its 90-day expiry, including
manual products without an import connection. Each scheduler pass queues at most 100
assessments and skips products with a queued or running assessment. Unchanged products retain their
approval and prepared matches while renewal runs; evidence still expires on schedule if
the worker cannot renew it. A failed renewal cycle waits one day before another cycle.
Changed product facts still require immediate reassessment, and a negative result clears
the previous qualification.

`PlacementPreflight` checks campaign capacity and destination availability in bounded
candidate batches before attribution locks. Exhausted campaigns and unavailable account/product
pairs are remembered only for that request. `campaign_delivery_usage()` shares receipt
reservation and UTC impression accounting with the final locked capacity check.
`PlacementDestinations` uses each account's first 20 owned listings and the same active
direct-product fallback as the locked resolver; sponsored articles use their internal preview.
These advisory checks never replace the final locked policy, destination and capacity reads.

Contextual issuance reruns the prepared-match query after locking the product and
returns the current relevance reason. It holds the matching worker's shared taxonomy
barrier until delivery commits, so relationship invalidation cannot remove a verified
match between the final read and reservation. A busy taxonomy editor stops the contextual
phase after one attempt; candidate rollback releases the new locks. Explicit fallback keeps its separate
context-independent eligibility contract.

The placement query-budget integration tests cover 105- and 205-product rejections,
later eligible inventory, lock release, and edits committed after preflight.

## Frontend gateway contracts

The admin, partner and user services register exposed routers with `include_gateway_router` from
`devfeed_http.operation_policies`. This supplies the default `GatewayPolicy` and
retains each endpoint's explicit `openapi_extra=GatewayPolicy(...).openapi_extra()`
override. Health routes outside these routers remain unexposed.
The user service uses `ReaderGatewayPolicy`, which also declares the cookie policy and
whether the operation requires the dedicated placement-query handler.
Its nullable `extension` policy controls extension eligibility, browser credentials,
request cache headers, forwarded headers and the total request timeout. An absent
extension policy denies the operation. Authentication routers default to denial;
identity, configuration and logout explicitly enable extension transport, while login
and callback run through the website sign-in flow.

`npm run admin:generate`, `npm run partner:generate` and `npm run web:generate` export the inventories
and regenerate the clients and transport definitions. The inventory includes actual
path converters, parameter constraints and any hidden operations. Generation rejects
unsupported or inconsistent contracts; normal frontend tests check the generated output.
Reader generation also compiles extension operations. Public reader adapters are
declared in `apps/web/src/lib/api/reader-bindings.json`, with distinct reader identities
and backend operation references. Generation derives their paths from concrete Next
files, validates method exports and backend references, and preserves their response
composition. The exported public operation catalogue records those backend identities
without exposing additional operations to the extension.

The admin gateway matches an operation in backend registration order. The partner portal
and reader use generated concrete Next route handlers under
`apps/partner/src/app/api/v1/partner` and `apps/web/src/app/api/v1/user`.
Next selects the route and method, and each handler binds one declared operation. There
is no partner or reader catch-all proxy or separate list of resource recognizers. Provider identifier
bounds belong to the backend's shared `ProviderSlug` type.

`GatewayRoute` from `@devfeed/http/gateway` validates parameters and encodes the upstream
path. `GatewayTransport` from `@devfeed/http/forwarding` shares origin checks, credential
and response-header forwarding, streaming, cancellation, and each operation's body and
upstream deadlines. Unsupported requests are rejected before body collection; backend
dependencies continue to enforce sessions, live membership and write permissions.

Reader operations forward only reader session, state and visitor cookies. Login alone
allows attribution cookies when the X pixel is enabled. Ordinary writes have a 1 MB
body limit and a two-second total reading deadline; avatar uploads allow 5 MiB plus
64 KiB of multipart overhead and ten seconds. Both use a 45-second upstream deadline.
Placement queries use an 8 KiB body limit and two-second reading deadline, with body
validation in their dedicated handler before forwarding. Public profile and leaderboard
handlers remain separate and omit credentials. Configured Chrome and Edge origins use
the same reader write-origin policy as the website.
Extensions compile the same parameter constraints and method declarations through
`GatewayRouter`. Their transport retains browser-managed HttpOnly sessions and never
copies cookies, credentials or provider tokens into extension storage. Request-body
collection observes cancellation and the declared body deadline before network fetch;
the extension's total timeout includes that collection time.

Reader writes use the named functions in `apps/web/src/lib/user-mutations.ts` on the
website and both extensions. Each function declares its acknowledgement, interest
invalidation and semantic analytics; `userRequest()` handles HTTP without interpreting
endpoint paths. Effects run after body parsing and acknowledgement validation, and
cancelled account-scoped writes emit no success effects. Analytics include explicit
identifiers, counts and enums rather than request bodies or profile information.

## Article publication lifecycle

`article_lifecycle.py` derives readiness from the article, retained source evidence,
current analysis and durable enrichment/image jobs. It does not persist another
stage flag or introduce a new queue. The scheduler and explicit admin recovery
use `reconcile_processing`; final publication uses the same readiness checks in
the locked editorial transaction. Approving an article does not bypass processing.

| Stage                | Requirement to advance                                                                                                |
| -------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Ingested             | An approved source origin and an original-page enrichment request                                                     |
| Enrichment           | A completed attempt for the current canonical URL                                                                     |
| AI analysis          | Current source and catalog evidence, resolved classification, an identifying title, readable overview and description |
| Images               | No active or failed current image work; managed variants complete when storage is enabled                             |
| Review               | Valid metadata, an active primary topic, relevant article classification, approved source, and editorial approval     |
| Ready                | Every publication prerequisite passes; manual or configured source policy may publish                                 |
| Published / Rejected | Terminal editorial outcome; normal reconciliation does not reprocess it                                               |

A successful lookup that finds no article image permits the reader's existing
fallback. Failed or unfinished discovery does not. Missing authors remain unknown;
the lifecycle does not invent a byline, source facts, a title prefix or filler prose.
There is no word-count minimum. Bare version/release titles require an identifying
rewrite supported by the retained article evidence.

Queued, running, delayed retries and exhausted failures remain visible within their
stage. Terminal failed jobs require an explicit retry; polling does not create an
unlimited stream of fresh attempts. Insufficient evidence stays blocked until its
inputs or review change. AI configuration and the existing content date window
remain prerequisites. Automatic source publication authority and the independent
article-derived topic proposal pause are preserved.

The admin Articles page offers a pipeline board alongside the existing table view.
Each card exposes blockers, current job links and retry timing. The article detail
has a readiness checklist and revision-checked recovery. Publication is enforced
by the server even for bulk review, CLI or stale clients. Active lanes include all
waiting ages; terminal lanes explicitly cover the last seven days.

Cards expose only the server's eligible processing retry targets: source enrichment,
AI analysis and images. Moving a card backward or selecting a retry queues that
task through its existing durable outbox; it never writes a stage flag. Requests
compare both the article revision and the selected current job ID. A delayed or
duplicate request conflicts after a new job exists, even if that job already finished.
The selected task cannot be retried while active or waiting for a provider cooldown;
an independent later task keeps its lease. Source changes still invalidate old AI
results before they can apply. A failed analysis retry carries verified successful
specialist results forward, while an explicit rerun of a completed analysis starts
a fresh run. Processing audits carry an explicit marker and do not create a new
human-review requirement.

Published-card retries validate source approval, the AI content date window and
the selected task's earlier prerequisites before atomically unpublishing and queuing
work. An image-only retry requires current enrichment and AI presentation evidence,
preserves that AI output and the original feed-publication timestamp, and returns
to Ready after image completion. Publishing again remains an explicit editorial
action and does not send another first-publication notification. Rejected cards
require an editorial decision before processing recovery. Existing image assets
remain intact during a forced refresh; an exhausted refresh blocks publication
until resolved, and stale discovery cannot replace a newer image URL.

Board counts use the same pure policy as admission. Current facts are loaded in
keyset batches, with current jobs selected through history indexes. Exact active
counts scan the waiting population; process-local readiness caching avoids loading
large extraction and inference snapshots again when their input identities, catalog,
article fields, settings and job state are unchanged. The publication transaction
never trusts this display cache. Historical terminal counts and paging stay in SQL;
only the displayed Published page loads current processing facts for retry actions.

Migration `0033` follows `0032`, adds the image-job history index and includes approved
unpublished articles in the automation due index. Analysis and enrichment reuse the
`*_latest` indexes from `0027`. The new index builds concurrently, with repair of an
interrupted build and a bounded metadata lock wait. This code supports schemas `0032`
and `0033`; source processing requires the columns introduced by `0032`.
Roll out compatible processes before applying `0033`. Its downgrade preserves parent
indexes and restores the pending-only automation predicate; older binaries still need
their own supported schema restored before they start.
The v9 specialist workflow applies to eligible unpublished work. It does not launch a
published-article backfill. Published content, URLs, feed order and notification
history remain intact. Explicit board reprocessing unpublishes an eligible Published
article in the retry transaction; other processing entrypoints require unpublishing
first. Editing old live titles/descriptions is a separate editorial operation and is
not performed by this migration or by viewing the board.

### Focused AI tasks

`article_analysis_steps.py` defines ten separate prompts and typed output contracts:
developer relevance, page eligibility, language, content type, content format, topic
matching, tag matching, identifying title, overview and search description. Each
task receives only its required context. Only topic/tag matching receives taxonomy;
description generation receives the accepted overview and source title. An already
identifying original title skips a model call. Explicit negative eligibility stops
unnecessary work, while an unresolved audience decision still permits the independent
page-kind check to identify a utility page.

The existing `ArticleAnalysisJob` remains the only queue/lease owner. Its `steps`
property persists copy-on-write progress in the existing `usage.article_steps`
JSONB field. Each task records its prompt version, input hash, model routing identity,
status, attempt count, result and safe failure code. Provider calls hold no database
transaction. The worker renews its lease between tasks, and its bounded RQ timeout
allows the sequence to finish. There is no separate per-task worker service.

Retries validate and reuse successful task results and repeat only the failed task.
An explicit retry carries successful work forward only when the source identity
matches; prompt, task context and routing changes invalidate affected work. Every
task has a finite retry budget. An exhausted or insufficient task leaves the article
unpublished. Only the fully validated aggregate applies article prose/classifications
in one transaction; individual task completion never writes a partial public article.
Manual corrections retain separately bound AI-generation evidence and cannot silently
trigger paid analysis that overwrites editorial authority.

The admin readiness view exposes task status, versions, attempts and retry times,
without inference inputs or draft outputs. Retention removes historical task outputs
along with private inference inputs while retaining operational metadata. Token
accounting includes all task calls. Focused prompts usually require more calls per
eligible article; early exits, deterministic title skips and result reuse bound waste.

## Object storage

`devfeed_core.object_storage.R2Storage` is the shared bucket-scoped R2 client. Install
`devfeed-core[storage]` in storage consumers and construct it with `from_settings()`.
The SDK is loaded only when storage is used. Use the context manager to close the client,
`exists` to check an object, `get(max_bytes=...)` for a bounded read, and `put`/`delete`
for mutations. Reads always close their response stream; missing objects return `None`.
Permission, bucket configuration and transient storage errors propagate to the caller.

Supply content type and cache policy when writing. Managed image pipelines use immutable
content-addressed keys, while avatar and custom ad-image uploads use fixed keys with
revalidation. Image availability and transformation settings remain in their adapters;
`ImageUploadStorage` owns compensation for partial writes and failed database commits.
The common client owns endpoint/credential setup, transport timeouts and bounded SDK retries.

`devfeed_core.image_processing.ImageProcessor` supplies image inspection and encoding.
Install `devfeed-core[images]` for these operations. Callers choose accepted formats,
pixel limits, orientation handling and whether encoding strips metadata.

Worker `OriginalStore` and `VariantStore` classes share storage operations while image,
logo and product subclasses retain discovery, normalization and key policies.
`ImgproxyTransform` owns signed transformation URLs and bounded renderer requests.
These stores borrow the pipeline's R2 client; the pipeline owns its lifetime.

HTTP `ImageUploadStorage` combines processing with fixed-key replacement and rollback.
`AvatarStorage` supplies square crops and avatar sizes; `ProductAdImageStorage` supplies
ad-image sizes and preserves aspect ratio. Storage allocation follows successful image
validation, and each upload context retains compensation until its database commit.

## Topic branding

`DEVFEED_AUTO_RESEARCH_TOPIC_BRANDING=true` enables a separate backfill for active
topics without `logo_url`, across every canonical kind, including platforms.
It also picks up newly approved topics after minimal identity review. The setting
defaults to false and requires AI configuration. At most two branding research or
verification runs are outstanding; each topic receives one automatic attempt.

Branding creates an audited update proposal and researches only a missing logo and
missing official website. It preserves identity, kind, description, aliases,
keywords and facts. Direct assets pass the public-network transport guards, a
2 MB limit and an image response check. Citations and the complete draft then
require independent verification before `DEVFEED_AUTO_APPROVE_TOPICS` can apply
the update. Otherwise the proposal awaits manual review. Concurrent catalog edits
block approval through the existing baseline check.

The research and verification calls each have a 16,000-token guard and a two-search
guard; existing finite job retries apply. Failed and inconclusive attempts remain
visible for manual review and are not automatically rescheduled. Operators can
explicitly request research again on a pending branding proposal. Polling never
resets the identity-decision budget. Branding proposals do not enter the minimal
identity or correction workflows, which would discard optional metadata.

Topics without a verified standalone logo retain a null URL and an explanation in
the research result. Parent-company logos, related products and generic icons must
not substitute for the exact entity. Existing logos are not overwritten.

No schema migration or API contract change is required. Enable the setting only
after the scheduler, topic-analysis and research-verification workers all run the
new code. Roll back by disabling admission first and draining branding jobs before
reverting workers: older bounded workers do not recognize branding-only requests.
