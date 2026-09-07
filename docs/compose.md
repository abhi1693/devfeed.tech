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

Only these two application ports are published, on every IPv4 interface by default.
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

AI and notifications are enabled only when configured in `.env`. AI needs the
endpoint, model and credentials described in [editorial](editorial.md). For Chimely,
set `DEVFEED_NOTIFICATIONS_ENABLED=true`, the API origin, environment slug, API key
and HMAC secret described in [notifications](notifications.md). Delivery workers
receive the management API keys; the admin API receives only its HMAC secret.
The web container receives neither credential.

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

## Build from this checkout

Use the build override when testing source changes locally:

```sh
docker compose -f compose.yaml -f compose.build.yaml build migrate admin-api admin
docker compose -f compose.yaml -f compose.build.yaml up -d --wait
```

These commands build three local images using the existing multi-stage Dockerfiles,
local BuildKit caches and the registry caches published by CI. BuildKit reuses layers
for the matching architecture; local builds do not write to the shared registry caches.
Use both files for subsequent commands on that installation.
The backend image is shared by the API, migration, worker and scheduler containers.
