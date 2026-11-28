# Public response query identities

Owned cached public endpoints opt into query normalization through their cache
policy. The route reads query parameters and aliases from FastAPI's registered
dependency graph once, at route construction. Unknown query parameters that the
endpoint ignores (such as tracking parameters) no longer create separate cached
copies of an otherwise identical response. There is no manually maintained route
or parameter allowlist.

Known query values are not coerced or reordered. Repeated values retain their
original order; aliases, dependency filters, path parameters and policy/schema
identities stay in the key. Requests with invalid declared parameters still run
native validation and are never replaced by a cached valid response. Oversized
query strings retain the existing cache-bypass limit.

Query-model and complex alias-path semantics stay conservative: their complete
query string remains part of the identity, including unknown keys that may cause
`extra=forbid` validation errors. Custom cached routes remain exact by default.
Only opt a policy into normalization when its response semantics depend on its
declared native query parameters, not arbitrary `Request.query_params` lookups.

A real PostgreSQL/Redis test sends 100 distinct ignored tracking-query variants
to a warmed public source detail. Every variant hits the same response cache and
performs no additional database query. This demonstrates avoided fragmentation;
it does not estimate how much production traffic uses such parameters or promise
a particular total CPU reduction.

Regression coverage includes aliased dependency fields, repeated-value order,
strict query-model rejection, invalid ordinary parameters, policy revisions,
mounted paths and unchanged publication invalidation. Run:

```sh
uv run pytest -q tests/infrastructure/test_cache.py tests/infrastructure/test_cache_integration.py
```

Integration cases require disposable services as described in
[the test guide](../tests/README.md). Before sizing production, compare CPU per
request, key/cardinality and hit ratios under a representative request mix.
This change introduces no migration, cache TTL or production manifest changes.

## PostgreSQL impact

A local x86-64 PostgreSQL 18 / session-mode PgBouncer / Redis comparison ran three
samples per policy with alternating order. The baseline uses the original exact
query identity (normalization metadata omitted); the candidate uses native declared
query metadata. Warm-up is excluded. A separate observer role captured all workload
PostgreSQL statements, including driver setup, read-only transactions and pooler
`DISCARD ALL`, rather than relying only on SQLAlchemy application SQL events.
The warmed pool has one existing backend, so backend startups were zero in both
modes. The fixture reads the same approved source detail with unchanged inputs.

| Metric | Before | After | Measurement basis |
| --- | ---: | ---: | --- |
| Cache outcomes for 100 tracking variants | 100 MISS | 100 HIT | Exact-query baseline versus normalization, three samples each. |
| API data SELECTs / all recorded PostgreSQL commands | 100 / 500 | 0 / 0 | Same 100 requests, including transaction/configuration/pooler commands. |
| Rows returned / shared buffer hits | 100 / 300 | 0 / 0 | Same tracking fixture; no reads, temporary spill or WAL in the candidate. |
| Read transactions rolled back / pooler resets | 100 / 100 | 0 / 0 | Read-only session completion, not failed requests. |
| PG planning + execution elapsed | 24.243 ms | 0 ms | Median of three tracking samples; elapsed time is not CPU. |
| PostgreSQL-container CPU | 130.147 ms | 18.659 ms | Median cgroup delta; includes logging, background work and probes. Candidate has no workload PG statements; the nonzero counter is not attributed to SQL. |
| Real Redis outage: commands / rows / buffer hits | 500 / 100 / 300 | 500 / 100 / 300 | 100 requests per mode, three samples; owned Redis paused, all requests bypass cache. |
| Real Redis outage: PostgreSQL-container CPU range | 122.921–130.143 ms | 127.980–133.433 ms | Three samples; overlapping noisy ranges with identical statement/row/buffer work. |
| Epoch churn: commands / rows / buffer hits | 100 / 20 / 60 | 100 / 20 / 60 | 20 requests per mode, three samples, public epoch invalidated before every read. |
| Epoch churn: PostgreSQL-container CPU range | 39.485–55.934 ms | 40.285–41.330 ms | Three samples; no PG work added by normalization. |
| WAL / idle transactions / remaining locks | 0 / 0 / 0 | 0 / 0 / 0 | All measured read scenarios. |

There is no new SQL path or schema change. The native policy regression exercises
both exact and normalized identities with available cache, unavailable cache and
public-epoch churn. The avoided work depends on actual irrelevant-query traffic;
these fixtures do not establish production container savings. Native ARM64 and a
representative traffic/pooler canary remain required before promotion. Equal work
under cache failure does not mean the database can absorb an arbitrary cache outage.
