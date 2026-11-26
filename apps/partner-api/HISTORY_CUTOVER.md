# Partner history cutover

Apply migration 0041 before copying history. Run `python -m devfeed_core.partner_history_import` with read access to the production ClickHouse database and a PostgreSQL connection for the destination. The import reads finalized observations and compares every source row with PostgreSQL before commit. It preserves the per-receipt identity and observation time and the revision of each manually replaced daily total. The same transaction recovers recorded delivery claims whose warehouse publication failed. Legacy claims already represented in daily totals are excluded. Repeating a successful copy does not add impressions or clicks.

A copy taken while the old application is serving is a rehearsal. Drain all old partner receipt and manual-total writers and retry pending writes before the final copy. Verify the final copy before enabling PostgreSQL report reads. Keep ClickHouse storage available until the reporting paths and AI admission controls have been verified after deployment.

## AI usage and admission

Drain inference workers and wait for in-flight provider calls and accounting writes to finish. After upgrading PostgreSQL to revision 0042, run `python -m devfeed_core.inference_history_import` with the same source and destination settings. The importer compares the trailing 24 hours of AI usage with PostgreSQL and opens count-based admission in the same transaction. Keep writers stopped through both final imports. Record both verified counts before starting the new workloads.

## Recovery

If either import fails, keep the application writers stopped, keep the warehouse available, and rerun the failed import after correcting its error. Before new PostgreSQL writers start, the old application can resume against its existing warehouse. After new receipts, manual totals or inference reservations commit in PostgreSQL, keep the new ledgers authoritative. Reverting the image alone at that point would omit those writes from old reports and budget decisions.
