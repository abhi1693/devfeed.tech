# Database connection recovery

All Python services share the database connection policy in `devfeed_core.db`.
API pools retain their configured size and overflow limits; workers can continue
using `DEVFEED_DATABASE_POOL_ENABLED=false` for `NullPool` behind a session-mode
PgBouncer service. Recovery does not require increasing the shared connection budget.

| Setting                                        | Default | Purpose                                     |
| ---------------------------------------------- | ------- | ------------------------------------------- |
| `DEVFEED_DATABASE_POOL_TIMEOUT_SECONDS`        | 2       | Maximum wait for a free local pool slot     |
| `DEVFEED_DATABASE_POOL_RECYCLE_SECONDS`        | 300     | Replace aged connections on checkout        |
| `DEVFEED_DATABASE_CONNECT_TIMEOUT_SECONDS`     | 3       | Bound a new connection attempt per address  |
| `DEVFEED_DATABASE_KEEPALIVES_IDLE_SECONDS`     | 5       | Start keepalive probes after TCP inactivity |
| `DEVFEED_DATABASE_KEEPALIVES_INTERVAL_SECONDS` | 2       | Interval between unanswered probes          |
| `DEVFEED_DATABASE_KEEPALIVES_COUNT`            | 2       | Limit unanswered probes                     |
| `DEVFEED_DATABASE_TCP_USER_TIMEOUT_MS`         | 8000    | Bound unacknowledged TCP data               |

TCP keepalives are explicitly enabled. These libpq options apply to both locally
pooled and unpooled connections. PostgreSQL documents their platform support and
semantics in [connection parameters](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-PARAMKEYWORDS).
They do not alter TLS verification or send unsupported startup options to PgBouncer.
The normal 30-second statement timeout is set after establishment in autocommit
mode, as required by the session pooler configuration.

SQLAlchemy's existing pre-ping checks idle connections before checkout. On a
detected disconnection it invalidates unusable pooled connections; later requests
open fresh ones. An operation interrupted during a transaction still fails. The
application does **not** replay statements, commits or entire requests automatically:
a lost commit response can leave its outcome uncertain. See
[SQLAlchemy's disconnect handling](https://docs.sqlalchemy.org/en/20/core/pooling.html#dealing-with-disconnects).

## Reporting isolation

Admin Overview calculations use a separate short-lived connection, not the
interactive request pool. At most one calculation runs per process across all date
ranges, including when Redis is unavailable or disabled. Excess work receives a
retryable 503 without waiting in the request thread pool. Existing Redis snapshots
remain private and expire after 60 seconds. A report transaction is read-only, has
a five-second per-statement timeout and a ten-second idle transaction timeout.
These are individual limits, not an overall report deadline.

The reporting connection closes after calculation so session-mode PgBouncer does
not retain another idle client. Readiness still tests the interactive pool: it must
not hide genuine request-pool exhaustion. Concurrency regression tests deliberately
hold a reporting SQL connection while exercising settings and readiness against a
one-slot interactive pool, both with caching enabled and disabled.

Production pool sizing must account for API replicas and processes, workers,
report refreshes, rollout surge and pooler failover. The ORM pool belongs to a
process, not to a page. Increasing every process's retained pool without budgeting
PgBouncer server slots can move the same bottleneck downstream. Background snapshot
refresh and transaction-pool compatibility are further architecture work; this
change isolates current on-demand reporting without claiming those migrations.

## Health behavior

All three APIs serve `/health/live` asynchronously without using the synchronous
request thread pool or contacting dependencies. It tests whether the process can
respond. A database outage makes `/health/ready` return 503 but does not deliberately
trigger liveness failures and restart every replica against the same unavailable
dependency.

Readiness uses the actual application pool, so an exhausted pool cannot be hidden
by a separate health-only connection. Pool acquisition now fails after two seconds
instead of the SQLAlchemy default of 30 seconds. Once connected, the schema check
has a transaction-local two-second statement timeout; closing the request session
rolls it back and preserves the normal query budget. Redis readiness remains part
of the check.

These are separate limits, not a single end-to-end deadline. A probe which first
encounters an unresponsive established socket can still exceed a five-second probe
timeout while the OS detects the failure and the driver attempts reconnection.
Subsequent requests recover as sockets are invalidated. DNS, multiple addresses,
Redis, server execution and pooler queueing can add time. A peer that continues
acknowledging TCP while its application stops responding is not a dead TCP path;
keepalives and `tcp_user_timeout` alone do not detect that application-level stall.

## Failure test

The opt-in integration test creates its own isolated Docker network, PostgreSQL
`recovery_test` database and pinned PgBouncer container. It never uses an existing
application database or mutates production resources. Run on Linux with Docker:

```sh
DEVFEED_TEST_DATABASE_FAILURES=1 uv run pytest -q -s tests/test_database_pooler_recovery.py
```

The Linux CI runner enables this test automatically through
`scripts/ci/python-tests.sh`; ordinary local integration runs remain opt-in.

The test uses two independent one-slot, zero-overflow engines behind real user API
readiness handlers. It checks the actual TCP socket options, starts an uncommitted
write and a long-running query, then disconnects PgBouncer from its Docker network.
Connecting directly to the container IP avoids a forwarding TCP proxy masking the
lost path by acknowledging packets itself.

Assertions cover bounded pool exhaustion, an interrupted transaction being
invalidated, an idle connection's pre-ping failing, continued liveness, and both
engines serving readiness again after the same pooler IP is restored. No API or
engine is restarted or disposed to recover. The test verifies the interrupted write
was not replayed and readiness did not leave its shorter statement timeout behind.
All test containers and the network are removed on exit. Unit tests also exhaust
all synchronous request thread tokens and verify each API's liveness remains usable.

## Rollout

The defaults require a rebuilt Python service image. Compose passes the optional
settings through from `.env`; manifests can override them with the same names.
An application source push does not update running production processes. Apply the
image through the normal release and Fleet workflow when a rollout is authorized.
Replacing currently stuck pods may restore service temporarily, but it is not a
substitute for deploying the connection policy and verifying dependency recovery.

## Query performance and input retention

Migration `0014` adds an alphabetical active-topic index and successful-job retention
indexes with concurrent builds. Public topic discovery uses an indexed, correlated
first-match lookup; feed options materialize their common visibility predicate once
for all facets. Visibility and each facet's independent filters remain unchanged.

Classification application takes a shared catalog lock, allowing independent articles
to finish concurrently while catalog edits remain excluded. Source-tag proposals use
a separate, short advisory-locked transaction before application. Catalog writers
retain their exclusive lock; callers must never upgrade a shared lock to exclusive.

The scheduler removes duplicate input/catalog snapshots from superseded successful
analysis jobs after `DEVFEED_JOB_PAYLOAD_RETENTION_DAYS` (default 30, minimum 7).
Each table is limited to `DEVFEED_JOB_PAYLOAD_PRUNE_BATCH_SIZE` (default 100) per tick.
It preserves results, hashes, usage, job IDs, decisions, failed/active jobs, each
subject's latest job, current article provenance and pending review/verification
inputs. The job's usage reports `input_payload_pruned_at`; these historical inputs
cannot be restored by downgrading the schema. Autovacuum reclaims reusable space;
this does not immediately shrink the filesystem or remove current evidence.

Deploy the migration before the new application image. Check API error rate,
connection-pool waiting, eligible backlog age and proposal duplication after rollout.
Set API and worker capacity in the deployment configuration, budgeting both normal
operation and pooler failover. Data repairs are separate operator actions; no
production record identities or one-off repairs belong in application source.
