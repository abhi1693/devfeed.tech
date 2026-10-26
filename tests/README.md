# Backend test organization

Python tests follow backend behavior domains. Larger domains have subpackages so
related tests stay easy to find:

| Package                              | Behavior                                                                                                          |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `admin/`                             | Admin authority, accounts, articles, sources, taxonomy, partners, operations, and reporting                       |
| `users/`                             | Authentication, accounts, agents, feeds, reader settings, recommendations, activity, discovery, and notifications |
| `partners/`                          | Portal authority, connections, catalog, matching, placements, promotions, media, attribution, and jobs            |
| `api/`, `http/`, `mcp/`              | Public API, shared HTTP/authentication behavior, and MCP contracts                                                |
| `articles/`, `analysis/`             | Article processing, publication readiness, analysis, and research evidence                                        |
| `sources/`, `discovery/`, `feeds/`   | Source admission and lifecycle, discovery, feed transport, and solvers                                            |
| `taxonomy/`, `images/`, `search/`    | Topics and relationships, image processing/storage, and search                                                    |
| `workers/`, `notifications/`, `cli/` | Scheduling and delivery, notifications, and operator commands                                                     |
| `infrastructure/`, `tooling/`        | Shared backend infrastructure, CI runners, profiling, and release tools                                           |
| `support/`                           | Reusable fixtures, builders, sample data, and stable repository paths                                             |

`conftest.py` owns suite-wide fixtures. Keep module-specific fixtures beside their
tests. Import reusable support explicitly, including any fixtures it depends on;
moving an autouse fixture into a broader `conftest.py` changes which tests it affects.
Use `tests.support.paths.REPO_ROOT` for repository files rather than depending on a
test module's directory depth. Shared static inputs remain in `fixtures/`.

Add a regression case to the suite that owns the behavior. Create a new file only
for a distinct responsibility or fixture lifecycle. Name it after that behavior;
avoid task history such as `remaining`, `runtime100`, or a coverage target. Keep
independent integration and unit suites separate when their fixtures or module
markers differ. Shared support should not import tests merely to obtain builders.

## Running tests

```bash
# All unit cases, or one behavior area
uv run pytest -m 'not integration'
uv run pytest tests/partners/connections -m 'not integration'

# Integration cases require explicitly configured disposable services
uv run pytest -m 'integration and not failure_recovery'

# Run the complete unit coverage gate used by CI
bash scripts/ci/python-tests.sh unit
```

The `integration` and `failure_recovery` markers determine CI selection. Directory
names and filename suffixes do not replace those markers. Some existing modules
contain both kinds of cases; retain per-case markers when moving them.

Integration runs require disposable `DEVFEED_TEST_DATABASE_URL` and
`DEVFEED_TEST_REDIS_URL`, plus `DEVFEED_TEST_CLICKHOUSE_URL`,
`DEVFEED_TEST_CLICKHOUSE_DATABASE`, `DEVFEED_TEST_CLICKHOUSE_USERNAME`, and
`DEVFEED_TEST_CLICKHOUSE_PASSWORD` for analytical behavior. PostgreSQL and
ClickHouse database names must end in `_test`; Redis must use database 15.
Fixtures truncate PostgreSQL tables and flush Redis. Migration tests also create
private databases, so the PostgreSQL test role needs `CREATEDB`, and the
ClickHouse test role needs database, table, and materialized-view creation and
cleanup permissions. These credentials must belong only to disposable services.

The session fixture upgrades PostgreSQL to Alembic `head`, including native
capture functions and bootstrap state. Analytical fixtures initialize ClickHouse
facts and serving generations; pytest does not require a separately running
exporter or report worker. Native migration `0039` is forward-only: use a fresh
disposable database when testing an earlier schema instead of downgrading through
the native cutover. Creating ORM tables alone does not install this pipeline.
Migration `0041` rebuilds only saved-report caches for explicit date-window keys;
date-range tests must run against that schema. It preserves source records and
retained analytical history.

CI discovers nested test paths and balances whole files across its workers. Moving
or merging files changes shard assignments but must retain cases, parametrizations,
fixture behavior, and unit/integration selection. The merged unit report enforces
the configured backend statement and branch coverage minimum.

`fuzz/` has a separate dependency group and invocation. `benchmarks/` contains
profiling workloads. The root JavaScript `*.test.mjs` files belong to their existing
Node runners and are independent of this Python layout.
