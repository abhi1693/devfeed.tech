# Retired PostgreSQL administration baseline

Historical evidence from the former PostgreSQL projection and saved-report architecture.
These measurements do not validate the current ClickHouse operator-card serving path.
The old workers and benchmark drivers have been retired; preserved artifacts describe
that implementation at the time measured. Current native historical evidence is in
[clickhouse-native-baseline.md](clickhouse-native-baseline.md).

## 2026-10-21 local results

These are historical measurements of the previous read-model contracts; the
Overview routes below have since been replaced. Current native ClickHouse
measurements live in [the native benchmark](../../benchmarks/clickhouse-native-baseline.md).

The complete run passed all 4,350 published requests: 50 samples for each of
29 routes in three phases. Every route met the 1,000 ms p95 budget and its query
ceiling. There were no HTTP errors in those phases. Values below are milliseconds;
the 16-concurrency column gives p50 / p95 / p99 for each route.

| Route                             | Concurrency 8 p95 | Concurrency 16 p50 / p95 / p99 | With publishers p95 | Max SQL |
| --------------------------------- | ----------------: | -----------------------------: | ------------------: | ------: |
| `article-attention`               |               532 |                473 / 751 / 785 |                 864 |      13 |
| `article-cards`                   |               459 |                427 / 628 / 775 |                 812 |      13 |
| `article-column`                  |               156 |                241 / 497 / 546 |                 400 |       6 |
| `article-deep-column`             |               155 |                277 / 526 / 625 |                 734 |       6 |
| `article-grouped`                 |               481 |                458 / 750 / 759 |                 745 |       6 |
| `article-search`                  |               491 |                459 / 777 / 802 |                 860 |      13 |
| `article-search-empty`            |               554 |                481 / 771 / 831 |                 897 |      13 |
| `article-source`                  |               550 |                487 / 756 / 834 |                 899 |      13 |
| `overview-adoption`               |               107 |                197 / 453 / 522 |                 544 |       5 |
| `overview-attention`              |               119 |                188 / 511 / 557 |                 312 |       5 |
| `overview-blockers`               |               116 |                216 / 271 / 443 |                 542 |       5 |
| `overview-decision-efficiency`    |               106 |                153 / 232 / 523 |                 238 |       5 |
| `overview-feed-health`            |               106 |                172 / 483 / 504 |                 517 |       5 |
| `overview-inference-tokens`       |               109 |                162 / 234 / 498 |                 237 |       5 |
| `overview-job-tokens`             |                99 |                150 / 228 / 431 |                 250 |       5 |
| `overview-main`                   |               129 |                188 / 522 / 566 |                 551 |       5 |
| `overview-popular-articles`       |               106 |                151 / 414 / 476 |                 240 |       5 |
| `overview-publication-automation` |               107 |                148 / 242 / 441 |                 255 |       5 |
| `overview-publication-time`       |               112 |                194 / 504 / 533 |                 512 |       5 |
| `overview-publications`           |               111 |                211 / 255 / 586 |                 532 |       5 |
| `overview-sources`                |                93 |                155 / 435 / 466 |                 264 |       5 |
| `overview-throughput`             |               117 |                160 / 208 / 417 |                 241 |       5 |
| `overview-topic-coverage`         |               103 |                164 / 453 / 462 |                 285 |       5 |
| `overview-workload`               |               116 |                193 / 321 / 536 |                 509 |       5 |
| `partner-dashboard`               |               182 |                251 / 543 / 553 |                 471 |       6 |
| `source-attention`                |               243 |                390 / 689 / 723 |                 765 |      14 |
| `source-board`                    |               235 |                421 / 712 / 744 |                 794 |      14 |
| `source-search`                   |               301 |                438 / 730 / 772 |                 825 |      14 |
| `source-type`                     |               247 |                407 / 718 / 741 |                 760 |      14 |

SQL counts use SQLAlchemy statement events; connection initialization, pre-ping,
and transaction protocol are excluded from that count and included in latency.
The worst route p95 was 554.385 ms at concurrency 8, 776.530 ms at concurrency 16,
and 898.757 ms with publishers. The competition phase reached 1,046.945 ms p99;
the configured gate applies to p95.

A clean rebuild produced 55,000 retained article rows, 2,000 source rows, and
500 unlinked-candidate rows in 390.857 seconds. The worker published all 16
requested reports in 7.461 seconds. The competition phase committed 400 ordinary
article edits, acknowledged all 400 events, and published 146 reports. During the
separate pause check, 100 duplicate events remained queued after all GETs and
drained in 1.600 seconds. Before and after replay, 32 sampled cards across 16
kind/stage groups matched the independent legacy board/lifecycle reference.

The profile process peaked at 370.4 MiB RSS; publisher subprocess high-water
marks may include process-launch inheritance and are not steady-state memory claims.
The final database was 5.18 GiB; pipeline projections occupied 150.1 MiB including
indexes and all report snapshots occupied 160 KiB. The original real-trigger
fixture load recorded 928.093 seconds across 23 table insert stages, excluding
commit/current-analysis setup; fixture setup is outside API latency measurements.

The reproducible local artifact is `/tmp/devfeed-read-models-profile-final.json`
with `complete: true` and `gates.passed: true`; its companion `.log` retains
per-request progress. These disposable artifacts are intentionally untracked.
This validates the local synthetic workload; production ARM64, PgBouncer capacity,
ingress, and browser performance remain deployment verification gates.
