# Daily Must Reads

Signed-in readers receive up to five articles from their prepared recommendation ranking.
The Gem button in the top navbar opens the selection at any time. On an eligible visit,
the selection opens automatically once per local calendar day. Existing dialogs,
onboarding, and article reading defer automatic presentation.

Desktop uses three columns and two rows: five article cards and a daily briefing tile.
Smaller selections leave out unavailable cards; tablet and phone layouts use two and
one columns. Readers can bookmark individual articles or save all unread picks.

The authenticated user API owns `GET /v1/user/must-reads?timezone=<IANA timezone>` and
`POST /v1/user/must-reads/presentation`. The reader supplies its browser timezone.
Snapshots persist ranked article IDs and explanations in `user_must_reads`, keyed by
account and local date. Recommendation refreshes do not replace today's selection.
Visibility, language, and content preferences are checked again on every fetch.
No public-feed fallback is presented as personalized content.

Presentation claims serialize against the account row and update only an unclaimed
snapshot for automatic presentation. Manual presentation remains available. Mutations
use the existing session, origin, and CSRF protection. Requests compute no recommendation
graph or AI content. Reading progress uses the existing account reading events.

Apply migration `0021` before starting the updated user API. The website and both
extensions share the navbar control, modal, and article actions. Browser regression
coverage lives in `scripts/testing/must-reads.mjs` and runs through the website reader
suite and the Chrome and Edge authentication suites.

## MCP access

The authenticated `get_my_must_reads(timezone="UTC")` tool uses `devfeed:read`
permission on the existing `/mcp` transport. Pass an IANA timezone such as
`Asia/Kolkata` to match the reader's calendar day. It returns the same stable daily
selection with article previews, recommendation reasons, read IDs, and preparation
status. It does not claim presentation, record article opens, or change streaks.
Agents cannot access the presentation endpoint, even with write permission.

## Admin inspection

The user Analysis page shows Today's Must Reads in place of its former top-five
ranked recommendation preview. `GET /v1/admin/users/{user_id}/must-reads` reads the
saved daily selection and reports its date, timezone, reasons, reading status, and
presentation timestamp. It shares publication and feed-preference filtering with
the reader. Admin inspection does not create a snapshot or claim presentation.
The default timezone follows the user's latest saved selection, then an explicit
appearance timezone, otherwise UTC. An optional IANA `timezone` query inspects
that zone's current date. A missing snapshot is reported as not generated.

## Daily Must Read browser notifications

Browser notifications are a separate, explicitly enabled channel. They send only
one personalized Must Read article per user per local day, using the highest-ranked
unread article in the same stable daily Must Reads selection shown by the reader.
Previously delivered articles are excluded. If recommendations are preparing or
there is no eligible unread pick, DevFeed waits or skips that day; it never replaces
the pick with a generic article or sends a backlog of missed days. Existing inbox
events are not sent as browser notifications.

The default delivery time is 09:00 in the browser's IANA timezone. Enrollment begins
at the next occurrence of that time. The oldest active browser registration defines
the account's daily timezone; all enabled browsers receive the same selected article.
Changing timezone or toggling consent cannot produce another daily article. Delivery
expires at the end of that local day. Browser/OS permissions, connectivity and Focus
settings can delay or prevent display; relay acceptance is recorded separately from
display and reading.

Signed-in website users enable or disable this browser in **Settings → Notifications**.
Permission is requested only after clicking Enable. Chrome and Edge extension settings
open the website's notification settings, keeping a single website-origin subscription
per browser profile without extra extension permissions. Notification clicks open the
selected website article. iPhone/iPad users must add DevFeed to the Home Screen and
open that web app before enabling notifications.

### Configure the sender

Web Push uses the browser vendors' standard relays with no notification SaaS account,
Firebase SDK or notification-provider fee. The existing PostgreSQL outbox, scheduler
and common RQ workers handle selection and delivery independently of Chimely. Run
database migration `0023` before deploying this feature.

Generate a key pair outside the checkout; the helper creates a mode-0600 file and
refuses to overwrite existing keys:

```sh
uv run python scripts/generate_web_push_keys.py \
  --output ~/.config/devfeed/web-push.env --subject mailto:you@example.com
```

For Compose, combine that file with the ordinary application settings:

```sh
docker compose --env-file .env --env-file ~/.config/devfeed/web-push.env up -d
```

For another deployment, provide these settings through its secret/config mechanism:

| Setting | Consumers | Meaning |
| --- | --- | --- |
| `DEVFEED_WEB_PUSH_ENABLED` | user-api, scheduler, delivery workers | Explicit feature switch, off by default |
| `DEVFEED_WEB_PUSH_PUBLIC_KEY` | user-api, scheduler, delivery workers | Base64url uncompressed P-256 public key |
| `DEVFEED_WEB_PUSH_DELIVERY_HOUR` | user-api, scheduler, delivery workers | Local hour, 0–23; default 9 |
| `DEVFEED_WEB_PUSH_PRIVATE_KEY` | Delivery workers only | Base64url 32-byte P-256 private scalar |
| `DEVFEED_WEB_PUSH_SUBJECT` | Delivery workers only | Contact `mailto:` address or HTTPS URL |
| `DEVFEED_WEB_PUSH_SITE_URL` | Delivery workers only | HTTPS reader origin; default `https://devfeed.tech` |

Keep the signing key stable across deployments and backups: existing browser
subscriptions are bound to its public key. Rotating keys requires browser enrollment
again. Signing credentials never go to the website or user API. Browser subscription
endpoints and encryption keys are not logged or exposed in subscription lists.

Registration and removal require the browser session, exact trusted origin and CSRF
token. Consent is bound to that session and is revoked on sign-out or account switching.
The sender checks the live session, current consent, article eligibility and daily
expiry before sending. Subscription URLs are restricted to supported HTTPS browser
relays, with DNS checks blocking private destinations; redirects and ambient proxies
are disabled. Invalid subscriptions are disabled on `404/410`; transient failures
use bounded retries within the same day's expiry. The website worker also checks its
consent binding and deduplicates the daily event before showing a notification.
A fresh consent token on re-enrollment prevents queued alerts from an earlier
account or consent period from appearing.
