# Native integration shards

CI runs six native ARM64 integration jobs. Each job starts its own disposable
PostgreSQL and Redis services and uses the same collected tests, duration profile,
`--ci-shard-count 6`, and a different index from zero through five. The existing
integration markers, fixtures, assertions, query budgets and timeouts still apply.

`pytest_shard.py` resumes its collection hook after pytest applies `-m` and `-k`.
It groups the selected cases by repository-relative test-file path, sorts files by
descending measured duration with a path tie-break, and assigns each file to the
currently lightest bin with an index tie-break. All parametrizations and module
fixtures stay together. Collection order and Python's randomized hash do not
affect the plan. Every runner must use the same revision, selection and shard count.

`integration-durations.json` records the sum of JUnit testcase seconds per file
from PR #202, head `c0fab34fb17bd58d0213466871214fc31b75fbf1`, workflow run
`39011793740`, attempt one. Its three successful artifact IDs are recorded in the
profile. The baseline contains 179 files and 2,184 cases, with no failures,
errors or skips. These counts describe the captured baseline; they never filter
the current collection. A newly collected file receives a positive 6.561-second
estimate, the baseline's median file duration, and participates in balancing.
Removed baseline files do not create work or reserve bins.

The profile must exist and pass validation before collection, even for a single
shard. Invalid paths, missing provenance, inconsistent baseline counts, and zero,
negative or nonfinite durations fail the run rather than omit work. The existing
`--ci-shard-index` and `--ci-shard-count` options remain usable with other counts
for local checks.

## Refreshing the profile

1. Select one successful full native integration workflow run for an exact source
   head. Download all of its integration JUnit reports from that same run and
   producer attempt. Record the head, run ID and artifact IDs.
2. Reject missing shards, duplicate testcase identities, overlapping test files,
   failures, errors and skips. Sum testcase times and case counts per test file,
   preserving module fixtures and all parametrizations. Use repository-relative
   paths, positive finite seconds, and the measured median as the unknown-file
   fallback. Update the baseline file/case counts with the captured measurements.
3. Run `uv run --no-sync pytest tests/test_ci_gates.py tests/test_sonar_coverage.py`.
   Review the deterministic plan for all six bins, including newly collected
   files, and obtain fresh hosted timings before claiming a speed improvement.

The initial six-bin estimate is about 399 testcase seconds per bin; this is a
model, not a measured job duration. Setup, queueing, changed test costs and fixture
order can affect actual time. Refresh weights from evidence instead of removing
tests, reducing scale fixtures or relaxing budgets.

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
