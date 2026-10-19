# DevFeed Admin API

Independent FastAPI administration/OIDC service, importing only the shared core
package. Source files live directly under `src`, mapped to `devfeed_admin_api`.

See [configuration, authentication, service boundaries and commands](../../docs/admin.md).

## Inference reporting totals

Migration `0031` backfills daily invocation totals and maintains them in the same transaction
as inference ledger inserts, updates, deletes and truncation. Full UTC days read those totals;
partial boundary days still read the ledger so arbitrary report windows remain exact. Numeric
validation, token subset accounting and the existing reporting budgets are unchanged.

The migration briefly blocks ledger writes while backfilling and installing statement triggers;
ordinary reads continue. It follows schema `0030`. Deploy the migration before relying on faster
reports; older compatible schemas use the original exact ledger query. Ledger writers incur
additional aggregate updates. Downgrade removes derived totals and triggers, retaining invocation
history. No API response or frontend change is required.

To reproduce report and writer profiles on a migrated disposable local database:

```bash
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://.../devfeed_profile_test \
  uv run python scripts/profile_inference_reports.py --seed --writes 100
```

`--seed` replaces existing test ledger/proposal/decision rows and their dependents. The
profiler refuses non-local hosts and database names without the `_test` suffix. It compares
complete 30/90-day report payloads and prints timings plus activity-query buffer statistics;
writer samples roll back their rows and temporary trigger changes. These local synthetic
measurements do not establish production latency.
