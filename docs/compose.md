# Run DevFeed with Docker Compose

This setup runs PostgreSQL, Redis, Chimely, the public API, background workers, the scheduler,
the anonymous user, and the admin website with its private API. Published images
are published for ARM64. On AMD64, use the local-build setup below.
For user changes that have not been published yet, use the local-build setup below.

## Start

Install Docker with the Compose plugin. For a fresh checkout, run these commands
from the repository:

```sh
cp compose.env.example .env
chmod 600 .env
openssl rand -hex 32
```

Put the generated value in `POSTGRES_PASSWORD` in `.env`. Use a hex password
so it can be included in the database connection URL. The file is ignored by Git.
If you already have a `.env`, keep it and add the Compose settings from
`compose.env.example` instead of copying over it. Compose defaults to its own
PostgreSQL and Redis containers. If `DEVFEED_DATABASE_URL` or `DEVFEED_REDIS_URL`
is already set, that value takes precedence: replace manual-development localhost
URLs with the internal URLs shown in the example, or remove the variables to use
the defaults. External connection overrides must be reachable from containers;
the bundled data services still start. OIDC settings go only to the admin API.

```sh
docker compose pull
docker compose up -d --wait
```

Compose waits for PostgreSQL and Redis, applies database migrations through a
one-off `migrate` container, then starts the application. A failed migration blocks
startup. A completed `migrate` container with exit code 0 is expected.
The one-off `chimely-db-init` service creates Chimely's database and restricted
login on the same PostgreSQL server, including when the data volume already exists.
It reconciles the Chimely database password on later starts without resetting data.
Then `chimely-provision` prepares the enabled local notification integration before
the worker, admin API and user API start. Both initialization jobs exit with code 0 on success.

| Open | What you'll find |
| --- | --- |
| <http://localhost:3000> | Public user: feed, search, topics, sources and article previews |
| <http://localhost:3001> | Admin website; configure sign-in below |
| <http://localhost:8000/docs> | Public API documentation |
| <http://localhost:8000/v1/feed> | Published articles; empty on a fresh installation |
| <http://localhost:8000/health/ready> | API, database, Redis and schema readiness |

These three application ports are published on every IPv4 interface by default.
Chimely also starts by default and publishes its dashboard on port 8082.
PostgreSQL, Redis and the admin API stay inside Docker networks. Data is stored in
named volumes; recreating containers preserves it. Redis uses append-only persistence for queued
work and sessions. Its data network is private and it has no host port.

## Enable search

The optional `search` profile runs Typesense with persistent storage and a dedicated
indexer. Configure separate management and query keys, enable search, and use the
local build override for unreleased changes or AMD64 machines. See the
[search guide](search.md) for setup, recovery and performance profiling.

## Access from another machine

Set the browser-facing admin URL and port in `.env`, for example:

```dotenv
DEVFEED_BIND_IP=0.0.0.0
DEVFEED_API_PORT=8000
DEVFEED_WEB_PORT=3000
DEVFEED_ADMIN_PORT=3001
DEVFEED_ADMIN_BASE_URL=http://192.168.1.101:3001
DEVFEED_ADMIN_COOKIE_SECURE=false
```

Open `http://192.168.1.101:3000` for reading, `http://192.168.1.101:3001` for administration and
`http://192.168.1.101:8000/docs` for the public API. Replace the example IP with
your Docker host's address. `DEVFEED_BIND_IP` may be a specific local interface
address, `127.0.0.1` for local access, or `::` for IPv6 on a compatible host.
The all-interface binding allows connections through any host IP; admin sign-in,
cookies and mutations use the single configured `DEVFEED_ADMIN_BASE_URL` origin.
Use that URL in your browser and register its exact OIDC callback. Do not put
`0.0.0.0` or `::` in the browser URL. If the base URL is omitted, its localhost
default follows `DEVFEED_ADMIN_PORT` automatically.

These settings use Docker's [port binding configuration](https://docs.docker.com/reference/compose-file/services/#ports).

## Enable admin sign-in

Add your dedicated OIDC application's issuer URL, client ID and organization ID to
`.env`, using the commented settings as a guide. Register the configured admin
origin followed by `/api/v1/admin/auth/callback`. For the default local origin:

```text
http://localhost:3001/api/v1/admin/auth/callback
```

Assign the required administrator role in your identity provider, then rerun
`docker compose up -d --wait`. See the
[admin guide](admin.md) for the exact role and organization claims. Without those
settings, the website loads with sign-in disabled; there is no default admin password.
OIDC credentials are passed only to the admin API, never to the public API or web container.

If port 3001 is in use, change both `DEVFEED_ADMIN_PORT` and
`DEVFEED_ADMIN_BASE_URL`, and register the matching callback. For HTTPS behind your
own reverse proxy, use your HTTPS admin origin and set
`DEVFEED_ADMIN_COOKIE_SECURE=true`. This Compose file does not provision TLS or an
identity provider.

## Application settings and integrations

Compose forwards the optional application settings from `.env.example`: CORS,
cache controls, feed/page limits, scheduler batch size, job logs and AI configuration.
Unset values retain the application defaults. JSON settings such as
`DEVFEED_CORS_ORIGINS` and `DEVFEED_OIDC_SCOPES` must remain JSON arrays.

Chimely starts with plain `docker compose up`; it does not need a profile. Its
`chimely` database and owner share the `postgres` container and `postgres-data`
volume with DevFeed's separate `devfeed` database. Chimely runs its own schema
migrations at startup. `chimely-db-init` is an initialization job, not another
PostgreSQL server.

To enable DevFeed's inbox integration, set these in `.env`:

```dotenv
DEVFEED_NOTIFICATIONS_ENABLED=true
CHIMELY_ADMIN_EMAIL=admin@devfeed.local
CHIMELY_ADMIN_PASSWORD=your-unique-password-at-least-12-characters
```

Then run `docker compose up -d --wait`. Compose provisions the local environment,
management key and subscriber HMAC automatically. The API URL and admin environment
default to `http://chimely:8080` and separate `devfeed-admin` / `devfeed-users` spaces; no manual key copying is needed.
Alternatively, generate bootstrap credentials and build local images with:

```sh
python3 scripts/compose_dev.py --notifications
```

This saves bootstrap settings to the ignored root `.env` with mode 600 and uses the
same Compose provisioning job. The job uses Chimely's authenticated admin API,
requires subscriber HMAC protection and verifies the inbox before it succeeds.
Issued management keys are reused after validating them against Chimely. Chimely's
dashboard is at `http://YOUR_HOST:8082/admin`; its login
is `CHIMELY_ADMIN_EMAIL` / `CHIMELY_ADMIN_PASSWORD` in `.env`. `CHIMELY_PORT`
changes the host port, and `DEVFEED_BIND_IP` applies to it too. Use HTTPS and
`CHIMELY_ADMIN_TLS_TERMINATED=true` behind your own TLS proxy; automatic local
provisioning uses HTTP.

Generated credentials are held in separate `chimely-worker-credentials`,
`chimely-admin-credentials` and `chimely-user-credentials` volumes, mounted read-only
by their consuming service. The worker receives management keys; each API receives
only its own audience HMAC secret.
Their entrypoints load the files before starting the application. For the bundled
integration these values take precedence over old credential values in `.env`.
Recreating the database causes provisioning to replace stale credentials before
consumers start. Ordinary starts reuse the existing environment and valid key.
An external `DEVFEED_CHIMELY_API_URL` or disabled notifications skip local provisioning
and retain the explicit environment configuration. A failed provisioning job blocks
consumer startup and reports its error through `docker compose logs chimely-provision`.
After changing Compose service definitions, restart an already-running `up --watch`
session so subsequent rebuilds use the updated dependency and credential mounts.
Run `python scripts/ci/check_chimely.py` to exercise fresh startup, retained storage,
and a replaced database in an isolated disposable project with real notification delivery.

The Chimely database password uses `CHIMELY_POSTGRES_PASSWORD` when set, otherwise
`POSTGRES_PASSWORD`. `chimely-db-init` synchronizes this password for both new and
existing Chimely logins, preserving the database and its contents. After changing
`CHIMELY_POSTGRES_PASSWORD` in `.env`, apply it with:

```sh
docker compose run --rm --no-deps chimely-db-init
docker compose up -d --no-deps --wait chimely
```

Use a hex password so it is safe in the database URL. This initializer only manages
Chimely's login; changing `POSTGRES_PASSWORD` does not rotate PostgreSQL's existing
`devfeed` login. The Chimely login cannot manage other roles/databases or read
DevFeed's tables.

Back up both logical databases on the shared PostgreSQL server. Their separate
schemas let DevFeed and Chimely manage their own schema versions.

DevFeed connects to `http://chimely:8080` inside Docker. Delivery workers receive
management keys; each API receives only its own audience HMAC secret. The web
containers receive neither credential. See [notifications](notifications.md) for an external
Chimely instance and operational details.

For AI, see [Codex in Compose](#codex-server-and-analysis-client) below. External
Codex endpoints still use the settings in [editorial](editorial.md).

For a service on the Docker host, use `host.docker.internal` instead of localhost,
for example `DEVFEED_CHIMELY_API_URL=http://host.docker.internal:8082`.
Compose adds the host gateway mapping on Linux. The host service must listen on
an interface reachable from containers; a service bound only to `127.0.0.1` is
not reachable this way. Alternatively, use a service name on a shared Docker
network or a reachable LAN/HTTPS origin. Compose does not start external services
merely because their URLs are configured.

## Add content and inspect the services

The CLI is available in the API container:

```sh
docker compose exec api devfeed sources add 'https://dev.to/feed' --type publisher
docker compose exec api devfeed status
docker compose exec api devfeed articles list --review-status pending --limit 25
docker compose ps -a
docker compose logs --tail=100 worker scheduler
```

Adding a source queues ingestion. Articles still need classification, review and
publication before they appear in the public feed; see the
[editorial workflow](editorial.md). No example sources or articles are added automatically.

## Worker concurrency

Compose starts two general RQ workers by default. Each worker handles one job at a
time, sharing the Redis queues. Database row locks, per-job lease tokens and
unique constraints guard against duplicate deliveries and stale workers applying
results. Completion order can differ from queue order; retries can still repeat
external requests after a crash or timeout.

Set persistent pool sizes in `.env`:

```dotenv
DEVFEED_WORKER_REPLICAS=2
DEVFEED_CODEX_CLIENT_REPLICAS=1
```

Apply the general worker count without restarting existing workers:

```sh
docker compose up -d --no-deps --no-recreate --wait worker
docker compose ps worker
```

With the bundled AI profile, general workers handle ingestion, enrichment and
notification delivery; `codex-client` consumes article and topic analysis. Set
`DEVFEED_CODEX_CLIENT_REPLICAS=2` and run the same `up` command for `codex-client`
to process two AI jobs concurrently. Keep one `codex-server`; its socket accepts
multiple client connections, with a separate analysis thread per job. More workers
increase memory, database connections and outbound requests. AI workers also share
the account's model capacity and can encounter more throttling.

The normal `up`, native watch and `scripts/compose_dev.py` all retain these replica
settings. A temporary `--scale worker=N` override applies to that invocation;
put persistent changes in `.env`. Every worker startup uses its container hostname
plus a random identifier as its RQ name. The name is saved in the container's
temporary filesystem so health checks verify that exact registration. This lets
the same container restart while an interrupted worker's old Redis registration
expires; no queue or job data is deleted. Do not assign a shared `container_name`.
The Compose file uses the service-level `scale` field. Compose 2.38.2 mutates
`deploy.replicas` while computing service hashes: a subsequent watch rebuild can
then scale unrelated worker pools down to one. Service-level `scale` retains the
configured count. Restart an existing watch session after changing these settings;
watch keeps its configuration in memory.
See Docker's [scale setting](https://docs.docker.com/reference/compose-file/services/#scale)
and [RQ's worker model](https://python-rq.org/docs/workers/).

## Update or stop

Back up your database before an update. Stop the application processes before
applying a new schema, leaving the database services running:

```sh
docker compose pull
docker compose stop admin admin-api api worker scheduler
docker compose run --rm migrate
docker compose up -d --wait
```

Proceed to `up` only if migration succeeds. Reapplying current migrations is safe.
The default `master` image tags move when CI publishes a verified image set. For a
repeatable installation, set the five image variables in `.env` to the
digest references from one [verified image manifest](ci.md). Keep all five images
on that same revision. Restoring an older image does not undo database migrations.
Changing `POSTGRES_PASSWORD` in the file does not update an existing database's password.

Stop and remove containers while keeping your data:

```sh
docker compose down
```

Adding `--volumes` to `down` deletes this installation's stored data. Keep that option
for disposable installations only.

## Build from this checkout and watch changes

Use native [Compose Watch](https://docs.docker.com/compose/how-tos/file-watch/)
to rebuild the affected images and recreate containers as you edit application
code, Dockerfiles or dependencies:

```sh
docker compose -f compose.yaml -f compose.build.yaml up --build --watch
```

If the setup script has already recorded both files in `.env`, the shorter command
works directly:

```sh
docker compose up --build --watch
```

The rules cover the user UI, admin UI/API, shared Python packages, workers, scheduler and
Codex server. Build output, caches, `node_modules` and `.next` are ignored. Native
watch uses image rebuilds, so the containers can keep their read-only filesystems.
Use only one watcher at a time. Ctrl-C on `up --watch` also stops its attached
services; `docker compose watch --no-up` watches an already running stack without
attaching to application logs.

If `up --watch` hangs after printing that all containers have stopped, use a
detached stack and a standalone watcher for subsequent runs:

```sh
docker compose up -d --build --wait
docker compose watch --no-up
```

Ctrl-C then stops only the watcher; use `docker compose stop` when you want to stop
the containers. If the stack is already running, only the second command is needed.

For database migrations or `.env`/Compose configuration changes, stop native watch
and use the coordinated rebuild command, then restart native watch. This ensures
application processes are stopped before migrations and configuration is reloaded:

```sh
python3 scripts/compose_dev.py
python3 scripts/compose_dev.py --watch
```

The Python helper also offers its own watcher that handles configuration changes
and migrations. It requires Python 3 and Docker Compose, and records both files in
`.env` (`COMPOSE_FILE`) so later plain `docker compose` commands keep using local
images. Remove that setting to switch back to published images. It builds all
replacement images first, waits for data services, stops application processes,
runs migrations, and recreates healthy application containers. Database/Redis
volumes are retained. A failed build leaves the current app running; a failed
migration leaves app processes stopped until corrected and retried.

The Python watch mode polls source/configuration files with a two-second quiet period. It
ignores generated builds, caches and dependencies, and catches edits made during
a build. Ctrl-C stops watching; containers keep running. A source change after a
failed build triggers another attempt. Changes to `.env` also trigger a rebuild.

BuildKit reuses dependency layers and local/CI registry caches for the matching
architecture. No images are pushed. API, migration, worker, scheduler and optional
Codex client images share one Dockerfile and cached layers, with a separate local
tag per service so native watch does not replace unselected services. The first build is slower;
subsequent source edits reuse cached dependencies.

## Codex server and analysis client

The `ai` profile supplies two services: `codex-server` (pinned Codex CLI 0.154.0)
and `codex-client` (an RQ worker alternating between the analysis and relationship
research queues). They communicate
through `unix:///run/codex/app-server.sock`. A small socket bridge in the server
container forwards to Codex's loopback listener. The socket has mode 600 and lives
in a volume shared with the analysis client and admin API; each container has independent process and
network namespaces, so native watch can recreate either service on its own. Codex
has outbound connectivity to OpenAI, its own persistent sign-in volume, and no
repository mounts, application credentials or published server port.
The regular worker uses `DEVFEED_WORKER_QUEUE=background` for ingestion and
notification delivery. It retains `DEVFEED_AI_ENABLED=true` so article enrichment
can queue analysis for the dedicated client. The old `DEVFEED_WORKER_AI_ENABLED`
override is ignored.
Before taking AI work, workers check that Codex answers its initialization and
`account/read` RPCs and has an account when its provider requires one. Missing
sockets, stopped or unresponsive servers, protocol failures, and missing sign-in
pause only analysis consumption. The worker stays registered and sends heartbeats;
article and topic jobs remain queued without claiming database leases or spending
their attempts. Readiness is retried every ten seconds, and processing resumes
automatically. Workers consuming `all` continue ingestion and notifications while
AI is paused; background-only workers do not make readiness requests.

The check makes no model request or token refresh. It uses the same Unix or
authenticated WebSocket transport as analysis. A job already in flight when the
server disappears can still fail and use its normal retry policy. Provider quota
or inference failures after a successful readiness check also keep their normal
retry policy. Burst workers exit after draining currently available eligible work,
leaving paused analysis messages queued for a later worker.

Start the AI services:

```sh
python3 scripts/compose_dev.py --ai
```

In the admin header, open **Connect AI**, then **Connect ChatGPT**. Open ChatGPT
and enter the displayed one-time code. The panel detects completion and also lets
you cancel or retry an expired sign-in. Device-code login must be enabled in your
ChatGPT account's security settings. CLI sign-in remains available with
`docker compose exec codex-server codex login --device-auth`.

The helper configures AI in `.env` and starts the app even before account sign-in,
so first-time setup can finish in the UI. Credentials stay in Codex's own volume;
the admin API only mounts the private socket, never the authentication volume.
`DEVFEED_CODEX_MODEL` defaults to `gpt-5.6-terra` on initial setup; select a model
available to your account. The credential persists in `codex-auth` across rebuilds.
To use an API key instead, feed it over stdin to the isolated container:

```sh
read -r -s OPENAI_API_KEY
printf '%s' "$OPENAI_API_KEY" | docker compose exec -T codex-server codex login --with-api-key
unset OPENAI_API_KEY
python3 scripts/compose_dev.py --ai
```

For continuous development with both integrations, after sign-in:

```sh
python3 scripts/compose_dev.py --notifications --ai --watch
docker compose logs -f codex-server codex-client
```

The server's HTTP health check proves it accepts connections; model access also
requires a valid account and selected model. Analysis validates structured results
against existing taxonomy IDs; topic changes still require operator approval.
The admin API checks the Codex protocol and account every 10 seconds and reconnects
after socket failures or server rebuilds. It checks ChatGPT account limits every
minute without running inference. The shared header distinguishes a server outage,
required sign-in, an account-service error, and an exhausted usage limit. The header
checks for updates every 10 seconds, independently of the table refresh settings.
Admin restarts cancel in-progress UI logins; start a fresh attempt if interrupted.
This account monitor lives in the single admin API process used by Compose. Keep
one admin API process per dedicated Codex server when managing sign-in through it.
Connection health does not test access to a particular model or the RQ queue;
job results remain the source for individual analysis failures.
The client verifies a named permissions profile that denies file and network access
before starting each analysis. It skips repository instruction discovery and disables
tools; the Docker container retains its read-only filesystem and dropped capabilities.
See the [official app-server documentation](https://learn.chatgpt.com/docs/app-server)
for the protocol and [editorial workflow](editorial.md#operator-workflow) to queue
an article for analysis. No paid inference is run by the rebuild command.

To analyze existing pending articles after enabling AI:

```sh
docker compose exec api devfeed articles analysis-backfill --limit 10 --dispatch
docker compose exec api devfeed articles analyses --limit 10
```

To rerun previously attempted articles, preserving their existing job history:

```sh
docker compose exec api devfeed articles analysis-backfill --limit 100 --dispatch --force
```

Inspect progress and results under **AI analysis** in the admin website. Backfill
requires an approved source and enough article text; it skips prior attempts for
the same input. For a specific article, use `devfeed articles analyze ARTICLE_UUID
--force` in the API container. If text is insufficient, run `devfeed articles enrich
ARTICLE_UUID --force` first. New article enrichment queues analysis automatically
when AI is enabled. Results remain subject to editorial review and explicit
publication. Topic and relationship research can use separate app-wide
[automatic approval settings](topics.md#automatic-approval); both default to off.

Optional user sign-in, followed topics, and My feed are described in [user accounts](user-accounts.md). Public browsing remains anonymous.
