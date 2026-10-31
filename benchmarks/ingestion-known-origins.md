# Repeated feed work

HTTP 200 responses often repeat known entries. The worker previously inferred
language for every parsed entry and performed one origin lookup per entry before
discarding the inferred value for origins it already retained.

The worker now reads the bounded feed's origin identities in one query, releases
that transaction, and runs language inference only for new entries. Persistence
still rechecks the current lease, source enablement/approval and feed URL. A fresh
batched origin read supplies the existing-entry path, including explicit tag
restoration and current editorial guards.

If a retained origin disappears while inference runs, the transaction rolls back
before article writes and the worker infers the complete bounded feed outside a
transaction. It then rechecks source/lease authority and applies once more. This
single bounded replay prevents a newly reimported article from losing language
metadata, without spending a queue attempt or holding database locks during Lingua.

The disposable PostgreSQL regression imports 500 entries and repeats the same
feed. It observes 500 inference inputs on the initial import, zero on the repeat,
and two batched origin reads for the repeat instead of 500 point lookups. The
repeat creates no articles or new origins. Another case deletes a retained
article during selective inference and verifies full off-transaction recovery,
three final origins and correct language on both newly imported articles.

```sh
uv run pytest -q tests/sources/test_ingestion.py \
  tests/sources/test_source_tags_integration.py \
  tests/taxonomy/test_languages_integration.py
```

Use disposable `*_test` PostgreSQL and Redis database 15 as described in
[the test guide](../tests/README.md). Tests truncate the configured databases.
Local runs are x86-64; native ARM64 CPU/RSS and production throughput remain
unverified. These input/query counts are removed-work evidence, not a total worker
CPU savings estimate. Legacy language backfill remains responsible for correcting
retained article languages; repeated feeds never previously applied that inference.
