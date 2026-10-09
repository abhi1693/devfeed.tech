# X conversion tracking

Tracking is disabled by default. Set `DEVFEED_X_PIXEL_ENABLED=true` on both the
web and user-api services to enable first-party ad-click attribution and server
signup conversions. The existing setting name is retained for deployment
compatibility; it no longer loads an X browser pixel. The web setting is read at
runtime, without a frontend rebuild.

Ordinary reader visits make no X browser requests and create no X third-party
cookies, including when tracking is enabled. There is no browser page-view event,
remarketing pixel, or browser signup event. Other configured analytics retain their
existing behavior. Chrome and Edge extension pages also keep their local scripts;
account creation uses the same website sign-in and server conversion flow.

## Completed sign-ups

In X Events Manager, create **Completed sign-up** with type **Lead** and
**Define with code**. Copy the full event ID from **View install code**.
In Manual setup, click **Next**, then **Generate access token**.
Set these on the **user-api** server (or the ignored `.env` for local development):

```dotenv
DEVFEED_X_PIXEL_ENABLED=false
X_PIXEL_TOKEN=
X_SIGNUP_EVENT_ID=tw-pc5f8-rgbo7
```

The event ID must have the form `tw-pc5f8-xxxxx`, with the real event suffix.
Compose passes the flag to web and user-api, and credentials only to user-api. Never put the token in public
frontend configuration. Restart user-api after changing these settings.

After the verified identity creates a committed account and a browser session,
the OIDC callback queues one background server request to
`https://ads-api.x.com/12/measurement/conversions/pc5f8`. Returning sign-ins,
failed callbacks and callback replays do not count as sign-ups. The unique
identity constraint also prevents simultaneous callbacks from counting twice.
The flag must be enabled and both settings present to send conversions.

When tracking is enabled, website landing pages capture a valid `twclid` query
parameter into a host-only, HttpOnly cookie for up to 30 days. A newer ad click
replaces it; the same click does not extend its lifetime. Ordinary visits without a valid ad-click parameter do not create this cookie. Only the
login gateway forwards the attribution cookies to user-api, which also accepts
the X pixel's URI-encoded JSON `_twclid` cookie for existing visitors.

At login, the server binds the click ID to the browser-validated OIDC flow in Redis.
Callback cookies and query parameters cannot replace that captured value. After a
successful new-account signup, CAPI receives it in `identifiers[].twclid` alongside
the provider email, trimmed, lowercased, and SHA-256 hashed. Either identifier can
be used alone; accounts with neither skip measurement. Click IDs never enter the
account profile or session response. Disabling tracking stops capture and use.

No phone number or IP address is sent. The conversion ID is `signup-{account_id}`.
There is no browser sign-up event; if one is added later, it must use this same
conversion ID to deduplicate with the server event. Server click attribution does not depend on browser access to X or third-party cookies.

Delivery has a five-second timeout and does not follow redirects. Errors never
invalidate the sign-up and logs exclude identifiers, tokens and response bodies.
Background delivery is best-effort, without durable retries; a process crash or
X outage can lose a conversion.

## Verification

Run `node apps/web/tests/browser/x-pixel.mjs` after `npm run web:build` to verify
that unset, disabled, and enabled configurations never load X browser resources,
including with third-party cookies blocked. The enabled configuration still
captures a valid landing click in a first-party HttpOnly cookie and forwards it
only to the login gateway. The browser test blocks all X requests.

For three fresh mobile and desktop Lighthouse samples against the local production
build, run `DEVFEED_X_AUDIT_STAGE=after node apps/web/tests/browser/x-pixel.mjs`.
Use `before` with the baseline build to record a comparison. Reports contain
scores and cookie names/domains only under ignored `reports/x-conversions`.
This optional audit contacts the real X browser endpoints if the tested build
loads them; it does not create accounts or send signup conversions.

After configuring the token and event ID, run:

```sh
DEVFEED_X_PIXEL_ENABLED=true uv run --locked python scripts/test_x_conversion.py --email test@example.com
```

Add `--twclid` with a real landing click ID to test click attribution.
Use an identifier you intend to send to X. This creates a real test event in
Events Manager; success prints `X Conversion API returned HTTP 200`. The browser
and HTTP transport regression tests mock X and never send live conversion data.

See [X's conversion tracking documentation](https://help.x.com/en/business-and-advertising/conversion-tracking-for-websites)
for event setup, identifier matching, Content Security Policy and deduplication.

## Measured comparison — 2026-10-10

Local production builds of `master` at `4c252771` and this change were tested on
`/legal/privacy`, a reader page that uses the same root layout as the feed. Both
used `DEVFEED_X_PIXEL_ENABLED=true`, disabled optional Google/Clarity analytics,
and the real X script/endpoints when requested. No ad-click query, synthetic
cookie, account creation, or signup conversion was involved.

Lighthouse 12.6.1 with Chromium 153 ran three fresh profiles per form factor:
mobile 412×823 at DPR 1.75 and desktop 1350×940 at DPR 1. No overall best-practices
score or production improvement is inferred from these two targeted audits.

| Audit                 | Before, all six runs  | After, all six runs  |
| --------------------- | --------------------- | -------------------- |
| `third-party-cookies` | Failed, seven cookies | Passed, zero cookies |
| `inspector-issues`    | Failed                | Passed               |

The baseline cookie names included `guest_id_marketing`, `guest_id_ads`,
`personalization_id`, `guest_id`, `muc_ads`, and `__cf_bm`. Cookie values, complete
measurement URLs, and tracking identifiers are excluded from saved reports.

This removes browser page-view/remarketing measurement. Intentional completed
signup measurement remains server-only, opt-in, and best-effort. If X is blocked
or unavailable, browsing, sign-in, and account creation remain independent of it.
