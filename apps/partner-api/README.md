# Partner API

An independently deployable, OIDC-authenticated service for partner reporting, placement
preferences, product controls, and catalog import connections.
See [portal architecture, setup, authorization, and rollout](../partner/README.md).
The service owns only `/v1/partner` and health routes and listens on port 8004. The public
API and reader sessions cannot access partner data. Its Docker image installs only this
service and its core/HTTP dependencies.

## Partner workspace

Workspace reads include every active account in the user's current memberships. They
filter and aggregate in SQL before pagination, without fetching one account at a time
or limiting access to the first account-picker page.

| Method | Path under `/v1/partner`       | Behavior                                                                        |
| ------ | ------------------------------ | ------------------------------------------------------------------------------- |
| `GET`  | `/dashboard`                   | Combined daily totals, trends, format metrics, and paginated performance rows.  |
| `GET`  | `/inventory`                   | Paginated products with independent placement policy for each account.          |
| `GET`  | `/connections`                 | Search, sort, and paginate connections across authorized accounts.              |
| `GET`  | `/connections/{provider}`      | Resolve one authorized connection and its account.                              |
| `GET`  | `/connections/{provider}/jobs` | Read that connection's paginated import history.                                |
| `POST` | `/connections`                 | Create a connection with an explicitly selected, authorized `account_id`.       |
| `GET`  | `/accounts`                    | Search and paginate the accounts available for settings and record association. |

Dashboard totals sum impressions and clicks before calculating CTR. Report and product
rows include `account_id` and `account_name`; the same product in two accounts remains
two independently actionable rows. Dashboard responses expose `account_total` instead
of presenting one account as the owner of combined metrics. Product lookup supports an
exact `product_id` filter, so bookmarked product pages can resolve authorized account
contexts without loading the catalog.

Optional `account_id` filters recheck access and return `404` for inaccessible accounts.
The account scope excludes paused accounts and revoked memberships on every request.
Record writes remain scoped to their owning account and use the same authorization and
revision controls described below. Account association in a creation form does not
change the scope of workspace navigation.

## Import connection API

Account-focused connection routes are beneath
`/v1/partner/accounts/{account_id}/connections`.

| Method   | Path                  | Behavior                                                                      |
| -------- | --------------------- | ----------------------------------------------------------------------------- |
| `GET`    | collection            | Search, sort, and paginate current connections with catalog statistics.       |
| `POST`   | collection            | Create an account-owned REST/JSON import and optionally start syncing.        |
| `GET`    | `/{provider}`         | Read connection settings, import status, and credential configuration status. |
| `PUT`    | `/{provider}`         | Update settings or replace a saved API credential.                            |
| `POST`   | `/{provider}/actions` | Request `sync`, `pause`, or `resume`.                                         |
| `DELETE` | `/{provider}`         | Disconnect and archive the connection.                                        |
| `GET`    | `/{provider}/jobs`    | Paginate import history with safe status and error summaries.                 |

The server generates each connection's identifier and fixes its account ownership at
creation. Partners cannot claim another provider identifier, reassign accounts, or change
commercial trust or partnership tiers. Multiple accounts can configure the same API
without sharing a connection or its credential.

Lists use SQL filtering and deterministic pagination with a maximum page size of 100.
Connection statistics are aggregated for the requested page. An account can have 25
unarchived connections, including paused connections. Import-management writes have
caller and account budgets of 30 per minute; creation also has an account budget of
10 per hour. Rate-limit responses include `Retry-After`.

Partner-configured connectors require a public HTTPS origin. API requests and redirects
use the existing public-address/DNS protections and remain on that origin. Definitions
support field mappings, filters, pagination, and public or authenticated JSON APIs.
Partner limits are 100 pages per import, requested page sizes up to 100, 60 API requests
per minute per connection, I/O timeouts up to 15 seconds, and 2 MB responses. Sync intervals
range from 15 minutes to seven days. Importing a product does not bypass qualification,
targeting, or serving eligibility.

## Authentication and concurrency

Requests require the partner role and a current partner session. Each account access
checks the user's immutable issuer and subject against a live membership in an active
account. Missing, foreign, and archived resources return `404`. Writes require the
configured partner origin and session CSRF token.

Connection updates, actions, and deletion require `expected_revision`. Stale revisions
return `409`; clients should preserve unsaved input while loading current settings.
Writes follow the catalog lock order and hold the account and membership rows through
commit, preventing a completed membership revocation from racing an authorized change.
Endpoint and credential changes fence outstanding import generations. Import workers check the
connection's enabled and archive state, sync revision, and job lease before accepting
results. Pausing cancels affected imports; shared product qualification can continue
when another active connection still owns the product.

## Write-only import credentials

Set `DEVFEED_PARTNER_CREDENTIAL_KEY` to a Fernet key generated with
`cryptography.fernet.Fernet.generate_key()`. Store the key in the deployment's secret
manager separately from the database and inject the same value into the admin API,
partner API and import workers. Compose passes it only to those services. Never expose
it to the Next.js application or browser, or commit it to the repository.

Bearer and API-key connectors receive their token in the write-only `credential` field.
The database stores authenticated ciphertext bound to the account, connection identifier,
HTTPS origin, authentication mode, and header. Workers decrypt it in memory for requests
to that origin. Connector snapshots and job payloads do not contain the stored credential. Portal responses expose
configuration flags rather than tokens, ciphertext or raw upstream diagnostics.

Omitting `credential`, or sending `null`, preserves the saved value; an empty string is
rejected. Supply a replacement token when changing an authenticated origin, mode, or
header. Explicitly changing authentication to `none` removes the saved credential.
Administration and the partner portal use the same encrypted per-connection storage.

Without an encryption key, the collection reports `credentials_available: false` and
authenticated setup fails with `503`. Public imports, pause, and disconnect remain
available. Invalid ciphertext, a missing credential, or a different encryption key fails
closed with a safe error. Replacing the encryption key requires replacing saved import
credentials.

## Pause and disconnect

Pause retains the connection and its saved credential so it can be resumed. Disconnect
archives the connection, disables imports, fences outstanding work, and removes any
partner-stored credential. Imported catalog records, measurements, and history remain
stored; disconnect does not erase reporting data.

An archived connection disappears from current lists and cannot be read, edited,
resumed, or synchronized through partner or admin connection routes. It no longer grants
product ownership or serving eligibility and does not count toward the account's
connection limit. Connecting the API again creates a new connection identifier.

## Catalog page limits

`connector.max_pages` accepts any positive integer with no fixed upper limit. Partner
connections default to 100 pages; the admin default is 1,000 pages. Increase the value
for larger catalogs. The importer still stops at the configured limit and rejects
repeated cursors; request rate, timeout and response-size limits continue to apply.
