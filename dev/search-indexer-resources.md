# Search indexer connection ownership

The search consumer is sequential: it reuses one indexer-owned PostgreSQL
connection, with no overflow, for its durable outbox and reconciliation work.
Sessions and transactions still end at the same boundaries. APIs and forked RQ
workers keep their existing engines and connection policy. SQLAlchemy pre-ping,
TCP deadlines and connection recycling continue to handle idle-backend loss.

`DEVFEED_SEARCH_INDEXER_CONNECTION_REUSE_ENABLED=true` is the default, even when
worker configuration sets `DEVFEED_DATABASE_POOL_ENABLED=false`. Set the indexer
flag to false and restart it to restore the previous configured connection policy.
Every exit, including a failed `--once` run, disposes the owned engine.

The consumer still checks an empty outbox after one second and backs off five
seconds on failure. Batch sizes, advisory ownership, remote-write ordering,
fanout cursors, acknowledgement and reconciliation freshness are unchanged.
Reusing a connection trades one idle PostgreSQL/PgBouncer slot for less setup CPU.

A local x86-64 comparison of 20 empty real outbox turns against disposable
PostgreSQL 18 executed 40 application statements in each mode. NullPool opened
20 connections and consumed 0.260 CPU seconds / 0.496 wall seconds. Reuse opened
one connection and consumed 0.042 CPU seconds / 0.090 wall seconds. These
observations exclude driver setup SQL from the statement counter and are not
native ARM64, active-backlog or production savings estimates.

Run the regression and measurement using the disposable-service environment in
[the test guide](../tests/README.md):

```sh
uv run pytest -q -s tests/search/test_search_indexer_database.py
```

The test also terminates only its own idle PostgreSQL backend and verifies the
next checkout reconnects. Use only disposable `*_test` databases and Redis DB15.
Before changing production requests or limits, compare native ARM64 CPU, RSS,
outbox age, document throughput, query latency and recovery against the same
indexed dataset. No production sizing or cluster manifests change in this PR.
