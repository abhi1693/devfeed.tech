# ClickHouse schema and resource budgets

Application analytics are stored in ClickHouse. PostgreSQL holds business data and
transactional action validation. Application processes publish sanitized observations
directly; the finite `analytics-importer` moves retained PostgreSQL history and
removes the retired analytical tables. See the
[analytics design](../../apps/admin-api/ANALYTICS.md) for delivery and recovery limits.

## Versioned schema upgrades

ClickHouse migrations are packaged with `devfeed-core` in
[`clickhouse_migrations`](../../packages/core/src/clickhouse_migrations).
`0001_schema_migrations.sql` creates the migration journal and
`0002_initial_analytics.sql` creates the initial tables and ingest materialized views.
The journal, `analytics_schema_migrations`, records the version, filename, SQL
SHA-256 checksum and application time in ClickHouse. There is no PostgreSQL
migration ledger for ClickHouse.

Run these commands with the deployment's schema-owner ClickHouse credentials:

```sh
uv run devfeed analytics current
uv run devfeed analytics upgrade
uv run devfeed analytics check
```

`current` reports applied and pending versions without modifying the schema.
`check` exits unsuccessfully if the installed history differs from this application
or has pending migrations. `upgrade` applies pending files in order, acknowledges
each completed file, and prepares the historical reader cutoff before application
writers start. A transient PostgreSQL advisory lock serializes deployment upgrades;
it stores no analytics or retry state. Historical scanning runs separately.

Compose's existing `migrate` service runs `devfeed db upgrade` followed by
`devfeed analytics upgrade` after ClickHouse is healthy. APIs and workers wait for
that command to succeed. The recorded Fleet migration contract follows the same
order; update the owning Fleet manifests to that contract before deployment.
Ordinary service startup and the historical importer do not create or alter the
ClickHouse schema. The importer checks its installed version before processing data.

To change the schema, add the next contiguous numbered SQL file, such as
`0003_add_publication_dimension.sql`. Separate statements with a standalone
`-- statement` line. Keep shipped files unchanged: checksum mismatches, missing
versions and unknown future versions reject the upgrade. Schema changes must be
safe to replay after partial completion because ClickHouse DDL is not a
transaction across the whole file. A failed file is retried on the next `upgrade`;
its journal entry is written only after every statement succeeds. Use a new
migration for corrections or object retirement. Keep grants in deployment-managed
roles, outside migration SQL. Migration SQL is included in wheels and runtime
images, so a source checkout is not required to apply an upgrade.

Each statement must be self-contained: the pooled client does not guarantee that
a preceding `SET` changes the connection used by the next statement. Put required
settings on the statement itself. A migration that starts background mutation or
data materialization must explicitly wait for completion before its version is
acknowledged; use the appropriate `mutations_sync` or `alter_sync` setting and
observable completion checks. See ClickHouse's
[ALTER completion rules](https://clickhouse.com/docs/reference/statements/alter#synchronicity-of-alter-queries).
Keep large historical scans and data backfills in the bounded importer, outside
schema upgrades and readiness hooks.

Back up ClickHouse, including its journal, import checkpoints and readiness state,
before an upgrade. Restoring an older application image does not reverse schema
changes. After the finite import removes PostgreSQL analytical history, rebuilding
an empty ClickHouse database cannot recover that deleted history.

## Credentials and admission limits

Compose mounts `resources.xml` and `users.xml` read-only. Generate distinct secrets
for each configured role in the private `.env`; never commit them. The configured
operator account retains administration and CH-UI access. Application services use
separate identities:

| Role              | Access                                                                               |
| ----------------- | ------------------------------------------------------------------------------------ |
| `devfeed_ingest`  | Schema migrations, finite import and analytical data maintenance; no user management |
| `devfeed_reader`  | Read-only analytical queries and CH-UI exploration                                   |
| `devfeed_public`  | Public search and partner events, publication/source observations and cards          |
| `devfeed_user`    | Reader opens, account/feed/membership observations and bounded open-total reads      |
| `devfeed_partner` | Partner reporting reads                                                              |
| `devfeed_admin`   | Analytical reads plus authorized publication/user/card/partner writes                |
| `devfeed_worker`  | Inference, publication/job/user/card observations and import-boundary reads          |

The exact table grants live in `compose.yaml` and `users.xml`. Application roles
cannot run schema migrations. For independently managed ClickHouse, provision
equivalent roles and pass each service its own normal ClickHouse credentials.
Credential changes require recreating ClickHouse and the affected services.

| Budget                    | Checked-in default                     |
| ------------------------- | -------------------------------------- |
| ClickHouse container      | 2 CPUs, 2 GiB                          |
| Tracked server memory     | 1.5 GiB                                |
| Concurrent server queries | 6 total, 3 SELECTs, 2 INSERTs          |
| Reader profile            | 2 concurrent queries, 512 MiB combined |
| Ingestion profile         | 3 concurrent queries, 384 MiB combined |
| Individual query          | 256 MiB, up to 2 threads, 20 seconds   |
| Historical importer       | 0.5 CPU, 512 MiB                       |

Service clients use up to two query threads by default in Compose. Server constraints
prevent clients from disabling or exceeding per-query time and memory limits.
Keep `SERVER_MAX_SELECTS` at least `READER_MAX_QUERIES + 1`, total query capacity
above the reader and ingestion allowances, and user memory budgets below server
memory. Local `.env` overrides can change these defaults. The authenticated
ClickHouse healthcheck uses the ingestion identity.

## Inserts and read costs

The application follows ClickHouse's
[insert strategy guidance](https://clickhouse.com/docs/concepts/best-practices/selecting-an-insert-strategy)
and [asynchronous insert guidance](https://clickhouse.com/docs/concepts/features/operations/insert/asyncinserts).
Source publication coalesces at most 200 changed identities, and historical import
reads at most 1,000 records per page. Native compressed inserts use
`async_insert=1, wait_for_async_insert=1`, acknowledging a write after its buffer
flush rather than after merely accepting it into memory. The application source
publisher uses a 50 ms busy timeout. Matching insert shapes can share buffering;
application batching still limits the number of small insert requests.

Current cards and source observations use stable identities and increasing
revisions; reports query their current replacing rows. Reader-open materialized
views retain lifetime identities and hourly audience aggregate states before raw
open rows expire after 30 days. These are ClickHouse ingest aggregates, with no
PostgreSQL analytical triggers, refresh tables or permanent projection worker.
Only Queue pressure polls after a successful overview load.

Monitor `system.parts` active part counts, merge work, query rejection counts and
`system.query_log` duration, read rows/bytes and memory alongside API latency.
`system.asynchronous_insert_log` exposes flush failures and costs. Measure the
catalogue, ingestion buffers, merges and concurrent reads together before raising
batch size, concurrency or memory. Leave background merges enabled; ClickHouse's
[OPTIMIZE FINAL guidance](https://clickhouse.com/docs/concepts/best-practices/avoid-optimize-final)
explains why routine forced merges add work instead of correcting excessive inserts.
