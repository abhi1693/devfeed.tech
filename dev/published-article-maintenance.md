# Published article maintenance

Published enrichment, analysis and image retries use a private `ArticleRefresh` owner.
Readers continue to see the last approved `Article`, its classification associations,
URL, original feed timestamp and notification identity. Only a complete, current
replacement can swap into that projection in one transaction. New unpublished
articles retain the existing unpublished processing path.

Selected jobs retain their private owner permanently. Ordinary readiness and retry
queries admit a promoted owner's exact selected artifact, not every job that ever
belonged to it. A discarded, replaced or withdrawn candidate cannot become ordinary
work after retention. Retention redacts old terminal payloads and job evidence using
the existing age and batch budgets; compact ownership and selected receipt identities
remain. Active and review inputs are protected. No original image objects are deleted
by this feature.

## Rollout

1. Keep `DEVFEED_PUBLISHED_ARTICLE_REFRESH_ENABLED=false` in the admin API and workers.
   While disabled, a published retry returns a conflict and preserves publication.
   There is no fallback to the old unpublish-and-retry behavior.
2. Apply the single schema 0040 migration. Its first transaction adds nullable private ownership;
   its second phase validates the new foreign keys with compatible locks and builds partial
   job indexes concurrently. Interrupted index creation is repaired idempotently on
   the next upgrade. A committed table comment identifies the completed schema phase;
   until every index succeeds, Alembic remains at 0039 and reruns only the remaining
   index work. Existing ordinary rows remain unbound.
3. Drain all old article enrichment, analysis and image workers; finish or stop their
   leases through the normal worker shutdown/recovery path. Upgrade the scheduler,
   APIs and every worker that can claim these jobs. Do not mix old workers with
   admitted private maintenance: old code does not understand the new subject.
   New admin clients submit published retries only to the maintenance endpoint.
   Old API replicas return 404/405 for that route; clients never fall back to the
   legacy pipeline retry endpoint during a mixed API rollout.
4. Verify every worker runs the same compatible release and schema check passes.
   Enable the gate on the admin API and compatible worker configuration together.
   Exercise one published article in each target, checking that detail/feed/sitemap
   still serve the old approval during work and failure, then expose only the complete
   replacement after successful completion or explicit review.
5. When replacement policy requires editorial review, open the private preview and approve
   that version or discard it with a reason. A changed version invalidates consent.
   Explicit unpublication, rejection, changed editorial revision or source approval
   loss always blocks promotion. Failed stages require an explicit selected-job retry;
   the scheduler does not repeatedly retry failed or insufficient work with unchanged
   inputs and catalog. Changed context can require fresh dependent processing.

The existing scheduler rechecks at most 20 due owners per tick. Its metadata-only
claim commits before processing starts; a crash leaves a five-minute due lease.
Stage completions also resume the next stage immediately. This recovers missed child
admission and final callbacks, and retries catalog/version races without repeating
completed valid specialist checkpoints. Existing queue overrides and workers apply.

Turning the gate off stops new published maintenance admission. It does not erase or
cancel admitted owners. Keep compatible workers until those owners finish or an
operator discards them. Once any owner exists, schema downgrade refuses to remove
ownership: removing it could turn surviving private history into ordinary work.
Rollback must use a compatible build with the additive schema retained. Do not deploy
a pre-feature worker while private owners or bound jobs remain.

## Legacy recovery review (dry run only)

This change does not restore previously unpublished articles. An older retry may
already have changed a live row, and `published_to_feed_at` alone does not establish
current editorial authority. No migration or scheduler bulk-publishes that cohort.

A recovery operator must first produce a bounded read-only inventory. Use an explicit
read-only transaction and a short statement timeout, select only IDs, status/revision,
original publication timestamp, origin/source approval, latest editorial audit metadata
and selected job/receipt identities. Do not copy article bodies, private inference
snapshots, credentials or provider errors into the report. Page by stable article ID;
report total and excluded counts separately.

For each proposed restoration, review:

- Evidence of a previous approved public projection and the exact legacy processing
  retry that withdrew it. A prior timestamp or successful job is insufficient.
- Every later human decision, source approval change, URL change and content edit.
  Explicit withdrawal or rejection excludes the article; unclear history requires
  individual editorial review.
- A complete recoverable approved projection, including current public associations,
  source authority, stored image receipts and applicable catalog policy. Current
  partially refreshed fields must not be described as the historical approval.
- Stable slug, original publication/feed timestamps and existing notification event,
  so recovery cannot appear as a new publication or send duplicate notifications.

The dry-run report must identify each proposed action, source of the recovered
approval, current revision, exclusions, expected public result and rollback limits.
Have that concrete report reviewed before any restoration is authorized. A later
implementation must use per-article guarded commands with source/editorial checks
and revision compare-and-swap, plus a small canary and public cache/feed/sitemap proof.
There is no authorized production restore, bulk SQL update or permission to infer
approval from pipeline success in this change.
