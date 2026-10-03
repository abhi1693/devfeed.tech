# X conversion tracking

Tracking is disabled by default. Set `DEVFEED_X_PIXEL_ENABLED=true` on both the
web and user-api services to enable it; `false` disables the browser pixel and
server conversions. The setting is read at runtime, without a frontend rebuild.

When enabled, the reader root layout loads the unchanged X base snippet for pixel `pc5f8` with
Next.js `afterInteractive`. Its nonce and the reader Content Security Policy allow
the X script and measurement requests on every website page. The admin site is
separate. Chrome and Edge extension pages keep their local scripts; their Manifest
V3 policy does not allow the remote X script. Account creation from either
extension uses the same website sign-in and server conversion flow.

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
replaces it; the same click does not extend its lifetime. Capture uses a separate
cookie so the X pixel can continue managing its own `_twclid` cookie. Only the
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
conversion ID to deduplicate with the server event. The base pixel independently
handles browser-side landing-page attribution.

Delivery has a five-second timeout and does not follow redirects. Errors never
invalidate the sign-up and logs exclude identifiers, tokens and response bodies.
Background delivery is best-effort, without durable retries; a process crash or
X outage can lose a conversion.

## Verification

Open the website with [X Pixel Helper](https://chrome.google.com/webstore/detail/twitter-pixel-helper/jepminnlebllinfmkhfbkpckogoiefpd)
and confirm pixel `pc5f8` fires. After configuring the token and event ID, run:

```sh
DEVFEED_X_PIXEL_ENABLED=true uv run --locked python scripts/test_x_conversion.py --email test@example.com
```

Add `--twclid` with a real landing click ID to test click attribution.
Use an identifier you intend to send to X. This creates a real test event in
Events Manager; success prints `X Conversion API returned HTTP 200`. The browser
and HTTP transport regression tests mock X and never send live conversion data.

See [X's conversion tracking documentation](https://help.x.com/en/business-and-advertising/conversion-tracking-for-websites)
for event setup, identifier matching, Content Security Policy and deduplication.
