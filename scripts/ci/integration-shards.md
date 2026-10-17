# Native integration shards

CI runs six native ARM64 integration jobs. Each job starts its own disposable
PostgreSQL and Redis services and uses the same current test collection,
`--ci-shard-count 6`, and a different index from zero through five. The existing
integration markers, fixtures, assertions, query budgets and timeouts still apply.

`pytest_shard.py` resumes its collection hook after pytest applies `-m` and `-k`.
It groups the selected cases by repository-relative test-file path, sorts files by
descending selected case count with a path tie-break, and assigns each whole file
to the currently lightest bin with an index tie-break. All parametrizations and
module fixtures stay together. Collection order and Python's randomized hash do
not affect the plan. Every runner must use the same revision, selection and shard
count. Newly collected files automatically participate; there is no file allowlist
or generated timing input to maintain.

Timing measurements remain in the uploaded JUnit reports. Test counts are a
scheduling estimate, not a prediction of execution time: setup, per-case cost,
queueing and module fixtures can affect actual job duration. Compare fresh hosted
runs before claiming a speed improvement. Six jobs also increase concurrent
runner demand. The existing `--ci-shard-index` and `--ci-shard-count` options remain
usable with other counts for local checks.

## Reports remain mandatory

Every shard uploads its own nonempty JUnit and Coverage.py database. The existing
JUnit gate rejects failed or skipped cases, and Sonar requires `python-unit` plus
all six `python-integration-0` through `python-integration-5` databases. Missing or
empty coverage from any shard fails coverage preparation. Each database is
combined; the regression suite gives every shard exclusive measured source lines
so an accidentally omitted database cannot pass unnoticed. `CI required` continues
to require the complete integration matrix.

Integration coverage measures the existing backend roots plus `scripts`, including
the sharding plugin's real configuration and collection hooks. Unit coverage's
backend-only report filters and minimum coverage requirement are unchanged.
