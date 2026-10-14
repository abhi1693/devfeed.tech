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
- `devfeed_http.body_limits.BoundedRequestBodyMiddleware` owns body size/deadline checks and
  replay. JSON and image-upload subclasses supply their route and error policies, preserving
  their different limits, deadlines and responses.
- `devfeed_partner_api.access.PartnerAccountAccess` binds a database session and verified
  identity. Portal, connection and workspace queries share its active-account and membership
  scope. Writes lock the account and the exact membership until commit, including preference
  saves. Product and catalog locks precede these authorization locks; rate limits, external
  requests and transaction commits stay with their existing callers.

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
