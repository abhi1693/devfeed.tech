# Partner API connectors

In **Partnerships → Partners → Create partner**, select **Custom API**, enter a name and
unique lowercase identifier, and configure the API. Nick Launches is available as a preset;
its saved definition can be edited like any other connection. The identifier stays fixed after
creation. Account assignment remains separate from portal membership and product placement.

Connectors use GET requests to a public HTTPS origin and interpret JSON as data. They support:

- List endpoints and optional detail endpoints containing `{id}`.
- Dot paths for nested response objects and numeric array indexes, such as `data.products`
  or `links.0.url`. Comma-separated paths are tried in order. An empty list path selects a
  top-level JSON array.
- Product ID, name, website, platform listing, description and pricing mappings. A listing
  URL template can supply the platform URL. Optional platform hosts distinguish platform URLs
  from product websites when an API swaps field meanings.
- Cursor, page number, offset, next-page URL, or no pagination. Cursor/next-page responses
  must include the configured next-token field, with `null` or an empty string on the last page.
  Page/offset modes use a has-more boolean, total count, or a short page to detect completion.
- Static query parameters and inclusion filters. Filter values match without case sensitivity;
  multiple filters must all match. Filters can include or exclude missing values.
- Per-connection request budgets shared across workers and previews, response size limits,
  timeouts and a maximum page count. Exceeding the page cap fails the generation rather than
  treating a partial import as complete and withdrawing unseen products.

The form shows an automatic API response preview beside the configuration. It fetches one
page independently of response mappings, samples up to three items per array, shortens long
values and redacts credential fields. Changing the endpoint or request settings refreshes it;
use **Refresh** to fetch it again. On narrow screens the preview appears below the form.

Use **Test connection** to fetch one page and map up to three products without saving a
connection, creating catalog products or queuing jobs. Review the mapped names, URLs and
descriptions before enabling the connection. Preview errors omit raw upstream responses and
credential values. Product imports still require the existing independent AI qualification;
API-supplied technology claims do not grant approval.

## Authentication

Choose **None**, **Bearer token**, or **API key header**. For authenticated APIs, enter a secret
reference such as `DEVFEED_PARTNER_SECRET_SHIPYARD`, and configure that environment variable on
both the admin API (for previews) and the worker (for imports). API key headers default to
`X-API-Key`. Enter only the environment variable name in the form. Secret values are never
stored in connector definitions or job payloads.

For local Compose, put partner secrets in an ignored `.env.partner-secrets` file and add an
ignored `.env.partner-compose.yaml` override:

```yaml
services:
  admin-api:
    env_file: .env.partner-secrets
  worker:
    env_file: .env.partner-secrets
```

Include this override with the usual Compose files when recreating those services. Compose's
CLI `--env-file` alone does not inject arbitrary variables into containers. In Kubernetes,
provide the variables through Secret references on both workloads. Changing an environment
secret requires restarting the affected processes; its value is resolved at request time.

Requests and pagination URLs stay on the configured origin. The shared HTTP client rejects
private/reserved DNS targets and IPs, pins resolved addresses and validates redirects. Credentials
are never forwarded to another origin. Definitions cannot execute Python, JavaScript or templates
beyond the literal `{id}` URL placeholder. Credentials embedded in URLs are rejected.

## Revisions and migration

Migration `0026` adds connection names and connector definitions, preserving the existing
Nick Launches configuration, products, account association and sync progress. Existing Nick
Launches discovery jobs receive a definition snapshot.

Each new sync stores its validated definition in the parent job. Product jobs use that snapshot.
Editing a definition cancels outstanding discovery/product jobs and, if enabled, starts a fresh
generation. Completed catalog records are retained. Changing only the interval, name or account
assignment does not alter an in-progress import definition. Concurrent edits use the existing
revision check.

This interpreter handles ordinary REST/JSON product APIs. POST/GraphQL requests, OAuth token
refresh, custom request signing and multi-step authentication need additional engine support or
an adapter. Shipyard or another provider can be configured once its actual endpoint and response
contract are available; the application does not assume an undocumented API contract.

Mapped paths found in the sample receive a colored border in the form and matching highlights in the JSON preview. Product fields resolve against each item in the configured list, using fallback paths in order. Pagination paths resolve against the response root. Hover titles identify matches without relying on color; unmatched sample paths remain neutral and may still be present in product details. Mapping edits update highlights without refetching the API.
