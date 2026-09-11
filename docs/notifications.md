# Notifications and Chimely infrastructure

Chimely is the independently deployed inbox service. DevFeed uses the same RQ
workers and scheduler as the other background jobs; there is no extra notification
worker service to operate. Toasts still acknowledge immediate UI actions.

The integration pins `@chimely/client` and `@chimely/react` to 0.2.2. The
[Chimely Dockerfile](../infra/chimely/Dockerfile) pins the matching published
multi-platform image by digest (Linux AMD64 and ARM64).

## Run the infrastructure

The bundled Chimely service starts with `docker compose up`, using its own database
and login on DevFeed's existing PostgreSQL server. With notifications enabled and
`CHIMELY_ADMIN_EMAIL` / `CHIMELY_ADMIN_PASSWORD` set in `.env`, ordinary Compose
startup also provisions the environment and credentials before consumers start.
To generate these bootstrap settings and build local images, run:

```sh
python3 scripts/compose_dev.py --notifications
```

This uses the same Compose provisioning job, stores runtime credentials in separate
worker/admin/user credential volumes, and uses `http://chimely:8080` between containers.
Stale credentials are replaced automatically if Chimely's database is recreated.
The dashboard is published
on the configured host interfaces at port 8082. See [Compose setup](compose.md)
for configuration, rebuild/watch commands and data retention.

### Standalone Chimely

These commands are manual instructions; nothing is started by installing DevFeed.
Use this alternative when Chimely is managed outside the DevFeed Compose stack.

1. Create a **dedicated database and owner** for Chimely on PostgreSQL 15 or newer.
   This may use the same PostgreSQL server as DevFeed, but not its application
   database. Chimely owns its schema and runs its own migrations automatically
   on startup. Use a direct connection or session-mode pooler, not transaction
   pooling. Back this database up separately.
2. Copy `infra/chimely/.env.example` to `infra/chimely/.env`. Fill `DATABASE_URL`,
   `CHIMELY_ADMIN_EMAIL`, and a unique `CHIMELY_ADMIN_PASSWORD` (12+ characters).
   Hosts must be reachable **inside the container**; `localhost` there is not the
   host machine. These native Chimely settings do not replace or conflict with
   `DEVFEED_DATABASE_URL` / `DEVFEED_REDIS_URL` in the application's root `.env`.
3. Optionally set Chimely's `REDIS_URL`. For a single-instance development setup,
   leave it unset: Chimely uses PostgreSQL LISTEN/NOTIFY for hints. For multiple
   replicas, configure Redis so rate limits and hints are shared across replicas.
4. Build and run the service from the repository root:

   ```sh
   docker build -f infra/chimely/Dockerfile -t devfeed-chimely:0.2.2 .
   docker run -d --name devfeed-chimely --restart unless-stopped \
     --env-file infra/chimely/.env \
     -p 127.0.0.1:8082:8080 \
     devfeed-chimely:0.2.2
   curl --fail http://127.0.0.1:8082/healthz
   curl --fail http://127.0.0.1:8082/readyz
   ```

   The example binds only to localhost. Use an SSH tunnel or an HTTPS reverse
   proxy to access it remotely; do not expose the operator dashboard to the
   internet over plain HTTP. When TLS is terminated by your proxy, set
   `CHIMELY_ADMIN_TLS_TERMINATED=true`. DevFeed's browser never connects directly
   to Chimely, so no public Chimely URL or CORS changes are needed.

5. Open `http://127.0.0.1:8082/admin` locally and sign in with the bootstrap
   credentials. Create a Chimely environment, e.g. `devfeed-admin`, with subscriber
   HMAC verification enabled. Create a management API key and retrieve that
   environment's subscriber HMAC secret. **Do not use `CHIMELY_DEV_*` bootstrap
   options**, which disable subscriber verification. Keep idempotency retention
   at 30 days or longer; DevFeed will not automatically retry after 28 days.

For image/configuration details see the upstream
[self-hosting documentation](https://github.com/dodopayments/chimely/blob/main/docs/content/docs/self-hosting.mdx)
and [authentication documentation](https://github.com/dodopayments/chimely/blob/main/docs/content/docs/auth.mdx).

## Connect DevFeed

For an externally managed Chimely, set these in the repository root `.env` after
provisioning the service. Replace the example credentials with that environment's
values. The bundled Compose integration loads its generated credentials automatically.

```dotenv
DEVFEED_NOTIFICATIONS_ENABLED=true
# For the local Docker command above, when DevFeed also runs on this host:
DEVFEED_CHIMELY_API_URL=http://127.0.0.1:8082
DEVFEED_CHIMELY_ADMIN_ENVIRONMENT=devfeed-admin
DEVFEED_CHIMELY_ADMIN_API_KEY=replace-me
DEVFEED_CHIMELY_ADMIN_HMAC_SECRET=replace-me
```

Use the **environment slug**, not its UUID. The API key and HMAC secret must both
belong to that environment. The common workers load the management API key;
the admin API loads the subscriber HMAC secret. Neither reaches the frontend.
There are no `NEXT_PUBLIC_*` notification secrets or additional app DB/Redis URLs.

When DevFeed runs in Docker Compose, these settings are forwarded to the consuming
services automatically. Replace the localhost URL above with a container-reachable
origin. For a Chimely service running on the Docker host, use
`http://host.docker.internal:8082`; the base Compose file supplies the Linux host
gateway mapping. Chimely must listen on a container-reachable host interface.
The localhost-only `docker run` example above needs a different published binding
or a shared Docker network before DevFeed containers can reach it. If both services
share a Docker network, use Chimely's service/container name and internal port.
See [Compose setup](compose.md#application-settings-and-integrations).

Install dependencies and prepare DevFeed's database, then run server commands in
separate terminals:

```sh
uv sync --all-packages --locked
uv run devfeed db upgrade

uv run devfeed scheduler
uv run devfeed worker
```

The normal `devfeed worker` consumes ingestion, enabled AI analysis and enabled
notification queues with round-robin fairness. Run more instances for concurrency.
`--queue ingestion`, `--queue analysis` or `--queue notifications` still allows an
explicit queue selection, but separate workers are **not required**.
The existing public API, admin API and Next.js commands do not change; see
[admin setup](admin.md). Chimely does not replace Zitadel/OIDC.

Sign in to DevFeed admin once to initialize that administrator's subscriber, then
run a feed fetch or enrichment. The bell shows meaningful completions, retries and
failures. Clicking a notification opens the related run, including its logs.
`Operations → Notification delivery` shows dispatch state and delivery logs.
The bell is hidden while configuration loads and when the admin inbox is disabled;
a configured-but-unreachable service shows a retryable error, not a false empty inbox.

## User notifications for followed topics

Signed-in users have a notification bell in the public header. New articles appear
in their private Chimely inbox when first published with an active primary or
supporting topic they already follow. Clicking an item opens the article preview
modal. The shared admin/user inbox provides a new-notification badge, read/archive
controls, tabs, pagination and live SSE updates through the pinned Chimely client. Hidden tabs stop the stream; returning refreshes the
inbox. Anonymous browsing does not request an inbox or require sign-in.

Users can open **Settings → Notifications** from the account menu or inbox footer.
The page follows the admin settings layout and save/reset behavior:

- Show unread badge (enabled by default) and notification sound (off by default).
- New articles from followed topics (enabled by default), stored as the Chimely
  `feed.topic.new` / `in_app` subscriber preference. Muting hides new and existing
  items in that category; re-enabling restores history. It does not unfollow topics.

Badge/sound settings use authenticated GET/PUT `/v1/user/settings/notifications`
and `user_accounts.notification_settings` (migration `0022_user_notifications`).
They survive sign-in and apply across devices. Writes require the user session and
CSRF token; callers cannot select another user's identity or the admin environment.
Sound plays only for new arrivals after browser interaction, never for initial
history. Display settings can still be saved when Chimely is unavailable, and the
form reports partial saves explicitly. Other tabs refresh on becoming visible.

The publication transaction records one `feed_notification_events` row containing
the first-publication timestamp and topic IDs. The scheduler expands at most 100
recipients from one event per tick, using indexed topic memberships and a persisted
user-ID cursor. A single bulk insert writes deduplicated delivery rows and commits
with that cursor; a failed transaction can be retried without skipping recipients.
Publication performs no fan-out or external HTTP. Completed events remain as audit
and delivery eligibility records. This adds migration `0018_feed_notifications`.

A user matching multiple topics receives one notification per article. Follows
created after publication do not receive old articles. Unfollowed, inactive and
incidental topics do not match; visibility and membership are checked again before
delivery. Changes after Chimely has accepted a notification do not retract that
existing inbox item; opening an unpublished article still respects the public API's
404 boundary. Republishing or reanalyzing an article does not notify again. Disabled
capture does not backfill history. Pending events expire after 28 days. Existing
outbox retries, leases and Chimely idempotency also apply to these deliveries.

## Audience isolation

Chimely environments are inbox security boundaries. Compose provisions separate
`devfeed-admin` and `devfeed-users` environments automatically when notifications
are enabled. Job events always target admin; followed-topic events are targeted
user notifications, never broadcasts. There is no credential fallback between them.

For externally managed Chimely, configure these values in the consuming services:

```dotenv
DEVFEED_CHIMELY_USER_ENVIRONMENT=devfeed-users
DEVFEED_CHIMELY_USER_API_KEY=replace-me
DEVFEED_CHIMELY_USER_HMAC_SECRET=replace-me
```

Management keys are worker-only. The user API receives only its user HMAC secret;
the admin API receives only its admin HMAC secret. Neither frontend receives keys or
HMAC signatures. The user API exposes `/v1/user/notifications/config` and a bounded
`/v1/user/notifications/chimely/v1/inbox/...` gateway. Both APIs share the subscriber
proxy implementation, derive audience-prefixed subscriber IDs from the verified
issuer/organization/subject, ignore browser-supplied identities and enforce session
and CSRF checks. A stream lasts at most 25 seconds before rechecking authentication.

To add an event, call the common helper inside the transaction that makes it true:

```python
from devfeed_core.notifications import NotificationMessage, enqueue_notification

enqueue_notification(
    session,
    event_key="release:0.1.0",
    audience="user",
    notification=NotificationMessage(
        category="product.release",
        title="New release",
        body="The latest DevFeed improvements are available.",
        action_url="/updates",
    ),
)
```

Omit `subscriber_id` for an audience-wide broadcast; provide the stable
`notification_subscriber_id(...)` result for one recipient. Event keys deduplicate
per audience/recipient. Chimely broadcasts are visible only to subscribers who
already existed when sent; announcements to future/new users require a separate
welcome event or a product announcements page, not replaying old broadcasts.

## Reliability and operating boundaries

- Domain job transitions append an outbox row in the same PostgreSQL transaction.
  Rollback means no notification. No external HTTP occurs inside that transaction.
- The common scheduler dispatches the durable outbox into RQ; targeted claims wait
  for dispatch locks. Worker loss is recovered after lease expiration.
- Chimely delivery happens after claiming/committing and without a held DB
  connection. A stable idempotency key handles timeouts, retries and duplicate RQ
  messages. Successful HTTP 200 replay and HTTP 201 creation both count as delivered.
- Retries back off (up to one hour, 20 attempts). Exhausted or expired deliveries
  stay visibly failed rather than disappearing. A failed delivery younger than
  28 days can be retried using the protected
  `POST /v1/admin/notifications/deliveries/{id}/retry` endpoint. This retains the
  same Chimely idempotency key. Older deliveries need manual review.
- Disabled notification capture does not create a historical backlog. An outage
  while enabled does retain pending deliveries. Readiness for DevFeed does not
  depend on Chimely being reachable.
- No-op polling stays quiet; new articles, changed metadata, analysis results,
  retries and failures are visible. These notifications complement retained runtime
  logs; they do not contain source bodies, credentials, raw errors or stack traces.
- Inbox REST/mutations are protected by the existing admin session and CSRF checks.
  The gateway allows only Chimely subscriber routes, not its management/operator API.
  Short SSE connections recheck authorization at most every 25 seconds and close
  at session expiry; ETags and periodic REST refresh recover missed hints.
- Chimely itself performs internal maintenance/hint processing in its own binary;
  it does not replace DevFeed's common RQ workers.

Monitor failed/old queued delivery runs, worker/scheduler heartbeats and Chimely's
`/readyz` and `/metrics`. Back up the DevFeed outbox and Chimely databases. Redis
runtime logs remain bounded troubleshooting records, not a permanent audit log.
