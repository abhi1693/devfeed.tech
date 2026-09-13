# AI cost and token optimization

DevFeed keeps the configured model and reasoning behavior. Optimization must not
remove source evidence, candidates, review gates, or exact-quote validation.

## Reanalysis

Automatic article requests coalesce on the existing article lock and active-job
constraint. Completed work is considered equivalent only when the article input,
model, prompt version, transport format and editorial revision match. Exact
candidate snapshots retain the existing exhausted-retry suppression. For applied
results with identical candidate coverage, the catalogue-equivalence check permits unrelated,
unselected fallback metadata changes. Relevant or selected identities still
invalidate the result. Explicit and forced requests remain available.

New requests record a reason in job usage: initial analysis, source content,
editorial, model, prompt, prompt format or catalogue changes, or manual/forced work.
The comparison is bounded to the most recent twenty compatible jobs; a miss causes
normal inference rather than incorrectly assuming equivalence.

## Prompt transport

`DEVFEED_AI_COMPACT_ARTICLE_PROMPTS` controls article JSON compaction and defaults
to `false`. The local quality gate did not justify enabling it automatically. It preserves
all selected candidates and their nonempty metadata, all source text, original
UUIDs and the output schema. Duplicate aliases/keywords and empty metadata are
omitted from the wire representation only; stored snapshots and validation still
use the full catalogue. Stable instructions precede the catalogue and article.
Cache hits require an identical rendered prefix and are not guaranteed.

Changing this switch records a different prompt-format identity. Workers record
the actual format used, including when a queued job predates the switch. Reverting
the switch restores the original prompt representation.

Short request-local identifiers were evaluated separately and are not enabled:
they saved more tokens but changed classifications and abstention behavior.

## Evidence reuse

Within a verification pass, citations already share a single fetch per URL.
Across passes, the private Redis namespace can retain parsed public evidence for
up to five minutes when the publisher provides HTTP validators. Every reuse still
performs a fresh SSRF-protected conditional request. A 304 must refer to the same
final URL; a fresh 200 replaces the content. Network failures, changed content,
expired entries, malformed entries and mismatched redirects never authorize stale
quotes. `no-store` and `private` responses are not retained. Global
`DEVFEED_CACHE_ENABLED=false` disables this optional cache.

This saves fetch/parse work; it does not skip model-based semantic verification or
assume a cached quotation proves a claim. Existing research job coalescing and
verification reuse retain their independent revision and review rules.

## Per-call usage

Apply migration `0008` before running this build. The `inference_calls` ledger is
separate from legacy cumulative job usage. It covers article analysis, topic and
relationship research, research verification and source relevance, including
failed invocations. Calls are registered before inference and finalized once;
worker termination leaves an unfinished row with unknown usage rather than
claiming zero spend.

Each row includes model, available reasoning effort, operation, job/attempt,
request fingerprint, request reason, start/end timestamps, duration, token counts
and web-search count. Unknown inherited reasoning settings remain null. No prompts,
responses, URLs, credentials or user identities are stored in this ledger. Job IDs
are retained without foreign keys so deleting a job does not erase accounting.
`returned` means the transport returned JSON, not that downstream evidence validation
or publication succeeded. Join the job ID to its durable outcome for quality audits.

Writes are idempotent. A usage-storage failure is logged as
`inference_usage_persistence_failed`; it does not convert successful inference into
a costly model retry. Those failures and unfinished rows represent accounting gaps.
No historical backfill is performed, since old job totals cannot reliably recover
individual calls and request timestamps.

Read usage from the explicitly configured database:

```sh
uv run python scripts/inference_usage.py --since 2026-09-13T00:00:00Z
```

The command uses a read-only transaction and bounded query. It reports actual
retained counters by operation, model, effort and request reason, plus an estimated
standard short-context API equivalent. Pricing is dated and must be updated when
rates change. It does not equate Codex subscription consumption to an API bill,
extrapolate monthly spend, double-count reasoning output, or add cached input on
top of total input. Unrecognized models have no price estimate. Reported costs
exclude unknown usage, context-tier premiums and any charges not represented in
the retained counters.

Batch API and API-key billing are separate transport choices and are not enabled
by these changes. GPT-5.5 and Luna are not selected automatically.

## Recurring evaluation

Use the [model benchmark workflow](model-benchmarks.md) before changing models,
prompts or reasoning effort, and for monthly comparisons. Its frozen cases,
per-task/domain reports and blinded human review replace one-off temporary scripts.
