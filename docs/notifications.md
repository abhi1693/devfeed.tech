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
worker/admin credential volumes, and uses `http://chimely:8080` between containers.
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

## Audience isolation and the future user app

A Chimely environment is an **inbox namespace/security boundary**, not necessarily
a deployment stage. One Chimely instance can host `devfeed-admin` and
`devfeed-users` on the same infrastructure in production. Broadcasts go to every
subscriber in their environment, so these namespaces must remain distinct.
An admin's read/archive state is independent of another admin's state.

The common event contract and delivery worker support both `admin` and `user`
audiences, targeted messages and broadcasts. Job events always target `admin`.
When the user app is built, provision its namespace and configure:

```dotenv
DEVFEED_CHIMELY_USER_ENVIRONMENT=devfeed-users
DEVFEED_CHIMELY_USER_API_KEY=replace-me
# For the future user API's session-bound subscriber gateway, not today's admin API:
DEVFEED_CHIMELY_USER_HMAC_SECRET=replace-me
```

The delivery worker never falls back to another audience's credentials. User
accounts, the user-facing API gateway and user inbox UI are **not implemented**
here. The admin proxy always derives an `admin_…` subscriber from the verified
issuer/organization/subject and ignores any client-supplied identity or environment.
The future user API should apply the same pattern with `notification_subscriber_id`
and `audience="user"`; it must never hand out administrative inbox identities.

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
