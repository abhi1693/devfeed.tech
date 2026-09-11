# Kubernetes deployment

Production manifests are owned by Rancher Fleet in the
[home-lab DevFeed bundle](https://github.com/abhi1693/home-lab/tree/master/kubernetes/projects/applications/apps/devfeed).
The application release is `v0.0.1`; its images are published only after the test,
security, architecture and runtime smoke gates in [CI](ci.md) succeed.

The public reader origin is `https://devfeed.tech`. Administration is restricted
to the internal Traefik origin `https://admin.devfeed.home`. Neither the admin API
nor Codex has a public ingress. The OIDC clients use separate IDs, callbacks and
secure cookies; the admin role requirement remains `superuser`.

Five analysis workers consume the analysis and relationship queues. Two background
workers consume ingestion and notification queues, with a single scheduler.
`DEVFEED_FULL_AUTOMATION=true` enables automatic research, reanalysis, tag linking
and approval where the application's evidence requirements permit it. Polling is
on for new sources, while ingestion still requires source approval.

Codex runs separately with its own ChatGPT sign-in and persistent home. On the
first deployment, its `chatgpt-login` init container presents a device authorization
URL and code in its logs. Complete that sign-in before inference becomes available.
The init container reuses existing credentials on subsequent starts. Credentials
are never mounted in workers or API containers. Workers and the admin API use a
separate bearer token and verified TLS; the app-server remains loopback-only behind
the private transport. Cluster clients receive only the certificate authority,
never the server private key. The Compose deployment retains its private Unix socket.

PostgreSQL roles, retained databases and a dedicated pooler are managed by the
home-lab database bundle. `DEVFEED_DATABASE_POOL_SIZE=1` and
`DEVFEED_DATABASE_MAX_OVERFLOW=0` bound each application process's pool. Migrations
use the direct database service. Redis persists queues and sessions, requires a
password, and uses the `noeviction` policy.

Chimely uses a separate database. A one-time job provisions separate user/admin
environments and writes each consumer's credentials to a separate retained volume.
Only notification workers receive delivery API keys; each account API receives
only its own inbox signing secret. Configuration and secrets are managed through
GitOps, with secret values encrypted using SOPS.
