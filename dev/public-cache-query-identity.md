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
