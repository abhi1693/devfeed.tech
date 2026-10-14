# Shared backend core

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
