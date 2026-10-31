# Ingestion heuristic catalog hydration

AI ingestion imported explicit publisher labels and then skipped heuristic
classification, but still hydrated every active Topic and every Tag as ORM objects
before checking any origins. Repeated HTTP 200 feeds paid the same cost even when
all entries were already known.

Catalog loading now occurs only when an entry reaches heuristic classification:
AI is disabled, the origin is new, and the retained article has no manual or AI
classification. Both catalogs are shared by the eligible entries in that feed
transaction. Publisher-supplied tags still resolve and attach on repeated imports.

A disposable PostgreSQL integration case seeds 10,000 active topics and 10,000
unrelated tags, imports two entries, and observes actual ORM load events:

| Mode | First import catalog ORM objects | Repeated import catalog ORM objects |
| --- | ---: | ---: |
| AI enabled | 0 | 0 |
| Heuristic classifier | 20,000 | 0 |

The test also confirms that both first-pass entries are inserted and the repeated
pass creates none. Existing suites cover heuristic classification, source tag
restoration, AI/manual classification protection, multilingual articles, source
changes, lost leases, and rollback/retry behavior.

```sh
uv run pytest -q tests/sources/test_ingestion.py \
  tests/sources/test_source_tags_integration.py \
  tests/taxonomy/test_languages_integration.py
```

Use disposable `*_test` PostgreSQL and Redis database 15 as described in
[the test guide](../tests/README.md). Tests truncate the configured databases.
The local validation ran on x86-64, not native ARM64 or production. Object-load
counts prove removed work; they are not a claim about total worker CPU savings.
The independent lightweight source-tag identity projection still runs when feed
labels need resolution. The changed path does not add a cache or migration.
