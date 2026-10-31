# Analysis catalog preparation

Production CPU profiles identified catalog normalization and trie construction as
worker hotspots. This change reduces repeated work inside one prepared generation;
it does not retain a catalog across jobs, skip revision checks, or cache decisions.

A bounded prefix detects repeated short ASCII vocabulary. Such generations use a
512-entry construction-only memo for values up to 128 characters; normalization
cannot expand ASCII beyond the two boundary spaces. Unicode and longer values use
the exact original normalization path. Mostly unique vocabularies skip the memo.
Trie construction also avoids allocating dictionaries for already-present edges.
The existing prompt/result caches and prepared-generation lifetime are unchanged.

## Local comparison

Python 3.12, x86-64, three cold constructions per variant; median process CPU.
Each fixture has two unique names/aliases, five keywords, and a short description
per row. Shared fixtures repeat the five keywords; unique controls use unique
keywords. The baseline is `origin/master` at `6f12ed1d`. No inference or SQL was
run, and these measurements do not establish whole-worker or production savings.

| Catalog rows | Vocabulary | Before | After |
| --- | --- | ---: | ---: |
| 10,000 | Shared | 0.319 s | 0.293 s |
| 10,000 | Unique | 0.459 s | 0.448 s |
| 50,000 | Shared | 1.811 s | 1.582 s |
| 50,000 | Unique | 2.685 s | 2.636 s |

The 50,000-row shared fixture used about 13% less construction CPU. An initial
unconditional weighted memo was rejected: it did not improve the shared fixture
and regressed the unique control by about 30%. The retained bounded probe avoids
adding memo overhead to that control. Timings are observations, not CI thresholds.

Run the analysis catalog suite to verify exact indexes/rankings for shared,
unique, Unicode and oversized terms, no persistent normalized vocabulary, and
rebuilds after edits:

```sh
uv run pytest tests/analysis/test_analysis_catalog.py -m 'not integration'
```

Before promotion, repeat representative catalog profiling on native ARM64 and
compare CPU, peak memory, inference admission/throughput, edits and publication
races under the same workload. No migration or production setting is required.
