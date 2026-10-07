# DevFeed partner portal

The standalone Next.js portal is intended for `https://partner.devfeed.tech`. It has its own
Python API in `apps/partner-api`, separate from the public reader, user API, and admin API.
Run `npm run partner:dev` (port 3002) and
`uv run uvicorn devfeed_partner_api.main:app --port 8004 --no-proxy-headers --no-access-log`.
Set `DEVFEED_PARTNER_API_URL=http://localhost:8004` and
`DEVFEED_PARTNER_BASE_URL=http://localhost:3002` for both processes. The API also needs the
normal database/Redis settings and `DEVFEED_PARTNER_COOKIE_SECURE=false` for local HTTP.
Compose and its build overlay include both services; only the frontend port is exposed.

## Access model

Create a separate Zitadel OIDC application in the DevFeed project with the exact redirect URI
`https://partner.devfeed.tech/api/v1/partner/auth/callback`. Configure the same organization,
issuer, and role claim policy as administration. Grant the application the `partner` and
`superuser` project roles. The API requests both role scopes and checks authenticated role
claims against the configured organization. An ordinary reader role does not grant portal
access. The partner API reads `DEVFEED_OIDC_CLIENT_ID` / `DEVFEED_OIDC_CLIENT_SECRET`; Compose
maps `DEVFEED_PARTNER_OIDC_CLIENT_ID` / `DEVFEED_PARTNER_OIDC_CLIENT_SECRET` into those values.
Use a separate process environment when launching the API directly.

Portal access requires either `partner` or `superuser`. A partner sees active accounts linked
by database memberships keyed by the validated issuer and immutable Zitadel subject ID.
One account can have many members; one member can belong to many accounts. Role access alone
shows an empty account state. Emails and requested account IDs never grant data access.
Membership deletion and account pausing take effect on the next data request. Unavailable
accounts return 404, including foreign account IDs. Zitadel role grants are captured at login
and bounded by the session lifetime; removing a role also requires revoking existing portal
sessions in Redis for immediate removal. Session cookies and Redis keys are isolated from
reader/admin sessions. Mutations require origin and CSRF verification.

Superusers need no membership and see active and paused accounts through the same account
selector. They can create accounts, edit tier/benefits, add or remove members, associate
catalog products or ads, and change asset status. Assign roles in Zitadel; membership forms
only control which partner accounts the user can access. Partners cannot manage grants,
tiers, assets, or measurement totals. Tier names and benefits are explicitly managed per
account; this change does not invent a billing plan or automatic feature entitlements.

## Measured value

Accounts own `partner_assets` (product placements or ads). Product assets reference canonical
catalog products; catalog providers and commercial partner accounts remain independent.
Each asset belongs to one account, keeping reporting attribution stable. Shared canonical
products can have distinct assets for different partner accounts. Membership, ownership,
and metrics are never inferred from an imported listing or its email/domain.

The dashboard shows impressions, clicks, CTR, per-asset results, daily activity, a 7/30/90/365
day selector, tier, and benefits. Periods use inclusive UTC calendar dates through today.
CTR is total clicks divided by total impressions, not an average of daily percentages.
Summary totals include all assets even when the asset table is paginated. A day with no
measurement is distinct from a measured zero; coverage and latest update are visible.
Revenue, conversions, and financial ROI are not claimed by these metrics.

There is no existing public partner ad/placement delivery pipeline in this PR. The catalog's
article relevance evaluations remain private and do not count as impressions or clicks.
Delivery integration must supply trustworthy daily totals before real partners see results.
A superuser-authenticated import can PUT
`/v1/partner/accounts/{account_id}/assets/{asset_id}/metrics` with
`{"day":"2026-10-07","impressions":100,"clicks":5}`. This atomically replaces an asset/day's
reported totals, so retries do not add duplicates. Negative values, future dates, foreign
assets, and partner-authenticated writes are rejected. It is not a public tracking endpoint;
reader events and advertising delivery still need a separate, reviewed measurement producer.

## Validation and rollout

Run `npm run partner:lint`, `npm run partner:test`, `npm run partner:build`, and
`npm run partner:test:browser`. Browser checks exercise the production Next build with a
controlled API fixture on desktop/mobile, role denial, partner and superuser views,
account switching, period selection, member management, and empty data states. Screenshots
are written to `reports/partner/`. Python auth tests validate signed mock-provider flows;
reporting and migration tests use disposable PostgreSQL/Redis.

The PR's unpublished `0021` migration now includes accounts, memberships, assets, and daily
metrics alongside the catalog tables. Apply it from `0020`; an older local draft `0021`
schema must be recreated in a disposable database before retesting. Downgrade removes all
partner data. Deploy the new images, configure Zitadel roles/client, and route the portal
hostname to the frontend. DNS, TLS, production deployment, live provider validation, and
measurement-producer integration are not performed by changing this PR.
