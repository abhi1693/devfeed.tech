# Run DevFeed with Docker Compose

This setup runs PostgreSQL, Redis, the public API, background workers, the scheduler,
and the admin website with its private API. It uses the published AMD64/ARM64 images
and selects your machine's architecture automatically. The reader website is still
planned; the web interface included here is for administration.

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

| Open | What you'll find |
| --- | --- |
| <http://localhost:3000> | Admin website; configure sign-in below |
| <http://localhost:8000/docs> | Public API documentation |
| <http://localhost:8000/v1/feed> | Published articles; empty on a fresh installation |
| <http://localhost:8000/health/ready> | API, database, Redis and schema readiness |

These two application ports are published on every IPv4 interface by default.
The optional notifications profile also publishes Chimely's dashboard on port 8082.
PostgreSQL, Redis and the admin API stay inside Docker networks. Data is stored in
named volumes; recreating containers preserves it. Redis uses append-only persistence for queued
work and sessions. Its data network is private and it has no host port.

## Access from another machine

Set the browser-facing admin URL and port in `.env`, for example:

```dotenv
DEVFEED_BIND_IP=0.0.0.0
DEVFEED_API_PORT=8000
DEVFEED_ADMIN_PORT=3001
DEVFEED_ADMIN_BASE_URL=http://192.168.1.101:3001
DEVFEED_ADMIN_COOKIE_SECURE=false
```

Open `http://192.168.1.101:3001` for administration and
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
http://localhost:3000/api/v1/admin/auth/callback
```

Assign the required administrator role in your identity provider, then rerun
`docker compose up -d --wait`. See the
[admin guide](admin.md) for the exact role and organization claims. Without those
settings, the website loads with sign-in disabled; there is no default admin password.
OIDC credentials are passed only to the admin API, never to the public API or web container.

If port 3000 is in use, change both `DEVFEED_ADMIN_PORT` and
`DEVFEED_ADMIN_BASE_URL`, and register the matching callback. For HTTPS behind your
own reverse proxy, use your HTTPS admin origin and set
`DEVFEED_ADMIN_COOKIE_SECURE=true`. This Compose file does not provision TLS or an
identity provider.

## Application settings and integrations

Compose forwards the optional application settings from `.env.example`: CORS,
cache controls, feed/page limits, scheduler batch size, job logs and AI configuration.
Unset values retain the application defaults. JSON settings such as
`DEVFEED_CORS_ORIGINS` and `DEVFEED_OIDC_SCOPES` must remain JSON arrays.

AI and notifications are opt-in. To run the bundled notification service:

```sh
python3 scripts/compose_dev.py --notifications
```

This builds local application images, enables the `notifications` Compose profile,
starts Chimely with its own PostgreSQL database, and provisions a subscriber-HMAC
protected environment through its admin API. Generated credentials are saved to the
ignored root `.env` with mode 600. Existing bootstrap credentials and environment
keys are reused. Chimely's dashboard is at `http://YOUR_HOST:8082/admin`; its login
is `CHIMELY_ADMIN_EMAIL` / `CHIMELY_ADMIN_PASSWORD` in `.env`. `CHIMELY_PORT`
changes the host port, and `DEVFEED_BIND_IP` applies to it too. Use HTTPS and
`CHIMELY_ADMIN_TLS_TERMINATED=true` behind your own TLS proxy; automatic local
provisioning uses HTTP. The native `infra/chimely/.env` is for a standalone deployment
and is not loaded by Compose.

DevFeed connects to `http://chimely:8080` inside Docker. Delivery workers receive
management keys; the admin API receives only its HMAC secret. The web container
receives neither credential. See [notifications](notifications.md) for an external
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
put persistent changes in `.env`. Workers use their container hostname as their
unique RQ identity; do not assign a shared hostname or `container_name`.
See Docker's [replica setting](https://docs.docker.com/reference/compose-file/deploy/#replicas)
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
repeatable installation, set the three image variables in `.env` to the
digest references from one [verified image manifest](ci.md). Keep all three images
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

The rules cover the admin UI/API, shared Python packages, workers, scheduler and
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

The `ai` profile supplies two services: `codex-server` (pinned Codex CLI 0.153.4)
and `codex-client` (an RQ worker consuming only the analysis queue). They communicate
through `unix:///run/codex/app-server.sock`. A small socket bridge in the server
container forwards to Codex's loopback listener. The socket has mode 600 and lives
in a volume shared only with the client; each container has independent process and
network namespaces, so native watch can recreate either service on its own. Codex
has outbound connectivity to OpenAI, its own persistent sign-in volume, and no
repository mounts, application credentials or published server port.
The regular worker uses `DEVFEED_WORKER_QUEUE=background` for ingestion and
notification delivery. It retains `DEVFEED_AI_ENABLED=true` so article enrichment
can queue analysis for the dedicated client. The old `DEVFEED_WORKER_AI_ENABLED`
override is ignored.
Workers using the local Unix transport also check that their socket mount exists
before consuming analysis jobs. A general worker without it handles the other
queues. A dedicated analysis worker fails startup so Compose retries without
consuming a job.

Set up the server and sign in to the dedicated instance:

```sh
docker compose --profile ai build codex-server
docker compose --profile ai up -d --wait codex-server
docker compose exec codex-server codex login --device-auth
python3 scripts/compose_dev.py --ai
```

Device login shows a URL and one-time code to enter in your browser. The helper
configures AI in `.env` and checks sign-in before restarting application services.
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
publication; analysis never activates topic proposals automatically.
