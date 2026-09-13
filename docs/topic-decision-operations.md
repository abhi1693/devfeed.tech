# Bounded topic decisions and model evaluation

Enable the new workflow only after reviewing a representative model benchmark:

```dotenv
DEVFEED_AI_BOUNDED_TOPICS_ENABLED=true
DEVFEED_AI_COMPACT_ARTICLE_PROMPTS=true
```

This also enables tiered routing. Full automation remains the policy controlling
automatic scheduling and reviews. `DEVFEED_AI_TIERED_ROUTING_ENABLED` can separately
enable task routing while retaining the old topic workflow. Both switches default
to false so deployment does not silently change the production model policy.
The September source-publication cutoff still applies to article work; topic
research remains eligible regardless of when a topic was proposed.

## Processing and evidence

Normal topic reviews use three calls: Luna medium discovers at most three primary
source URLs, Luna low drafts an exact identity, kind and short description, and a
fresh Luna medium call independently verifies identity, each claim and developer
relevance. Optional aliases, keywords, facts, logos and website fields are omitted
from the minimal draft. The before/after metadata and supporting evidence remain
audited. No model response directly creates an active topic: the existing review
service, identity collision checks and automatic-approval policy still apply.

Public source pages pass the existing SSRF-safe transport. One bundle of bounded,
timestamped excerpts is saved per review. Models select sentence IDs; code retrieves
the exact visible quotation. This removes quote-copying failures, but quotation
presence alone never proves semantic support. The independent verifier must still
check the exact identity and every retained claim. Snapshots expire after one day.

A failed draft, invalid evidence selector or failed semantic verification permits
one Terra medium correction, followed by another independent Luna verification.
No new browsing or fetching occurs during that correction. Incorrect or ambiguous
identity stays pending. Rejection requires an independently evidenced out-of-scope
identity; exhausted processing is never a rejection. Article/source relevance uses
Luna low; relationship research and verification use Luna medium. Article validation
failure can escalate on the second attempt, with a terminal job failure if that
validated retry also fails. Transport failures do not authorize escalation.

## Budgets and recovery

Migration `0009` adds `topic_decision_runs`. New workers and APIs require this schema.
Run the migration through the pre-upgrade hook before starting these workers. The
v0.0.9 API requires schema `0008`, so allow a readiness interruption between the
migration and replacement API readiness; do not treat this as a zero-downtime schema
change. No migration rewrites topic decisions or resets existing article reviews.

The first run under this policy gets five calls, 64,000 total reported tokens,
four hosted search operations, one escalation and 240 seconds of processing time.
Each call reserves up to 16,000 tokens before transport starts. Counts span all
stages and retries, survive automatic metadata changes, and are stored separately
from best-effort inference telemetry. Earlier legacy spending stays in the existing
job and per-call ledgers; the new policy grants one fresh bounded allowance.

Admission stops new calls when a limit is reached. Streaming token/search limits
interrupt an over-budget turn and discard partial output. These are operational
limits, not provider billing caps: notifications and cancellation can arrive after
spending occurs. Unmetered or interrupted invocations retain their full reservation.
The system does not turn missing token telemetry into free usage. Calls with a
lost lease require review rather than being issued again. Network time spent
fetching evidence also counts toward the processing allowance.

The scheduler prioritizes actionable topic reviews. Background relationship jobs,
including already queued jobs and verifier calls, pause while that backlog remains.
After it drains, a separate, atomic rolling 24-hour allowance permits 40 relationship
inference calls. Deferred/manual-review topics do not block that queue forever.
Existing graph coverage cursors remain intact; pausing does not mark coverage done.

Deferred topics show their reason in the job and the Overview charts. Review them
manually or explicitly grant more capacity; ordinary edits and retries do not reset
the allowance:

```sh
uv run devfeed topics grant-decision-budget PROPOSAL_UUID \
  --calls 3 --tokens 32000 --actor operator-name --reason "Capacity restored"
```

This creates a new job, clears stage checkpoints for fresh evidence, and preserves
all consumed/reserved usage. The additional allowance and actor are audited. At
most two grants are accepted before manual review is required. Changing defaults
only affects new runs; it cannot silently replenish old budgets.

## Recurring benchmarks

`scripts/topic_workflow_benchmark.py` compares complete workflows, not one prompt
or agreement with Terra. The plan in `benchmarks/topic-workflows.json` includes
legacy Terra prompts/validators, the bounded workflow using Terra throughout, and
the bounded workflow with tiered routing. This separates workflow savings from
model savings. No GPT-5.5 or paid-provider fallback is enabled.

Prepare a private JSON array of cases with `id`, `proposal_id`, `domain`, `topic`,
`provenance`, and an `evidence` bundle from `devfeed_core.topic_decisions.fetch_bundle`.
Include easy and difficult languages, databases, infrastructure, security, generic
concepts, ambiguous names and out-of-scope entities. Freeze the same inputs for all
candidates. Keep held-out cases separate from prompt-tuning examples and record
human judgments independently of model outputs.

```sh
# Preview only: no inference, database writes or provider calls.
uv run python scripts/topic_workflow_benchmark.py \
  --cases reports/topic-cases.json --output reports/topic-benchmark-new

# Explicit local experiment using a configured Codex app-server.
uv run python scripts/topic_workflow_benchmark.py \
  --cases reports/topic-cases.json --output reports/topic-benchmark-new --execute

# After completing review-template.json with independent human review:
uv run python scripts/topic_workflow_benchmark.py \
  --cases reports/topic-cases.json --output reports/topic-benchmark-new \
  --reviews reports/topic-benchmark-new/reviewed.json
```

Default frozen mode disables tools and provides the same saved evidence to each
workflow, including URL discovery. It evaluates schema compliance, identity/scope
decisions and factual support while controlling page drift. `--live-evidence`
instead measures actual browsing/fetching and the legacy pipeline's repeated
research. Compare frozen runs with frozen runs and live runs with live runs. Frozen
evidence is evaluated as of its capture timestamp, not falsely marked as freshly
retrieved. Per-case execution, global calls, randomness and model prices are bounded
by the plan. Use a new report directory for each experiment.

Reports retain decisions, incomplete work, tokens across every stage, escalation
counts, decisions/hour, and API-equivalent cost using explicitly dated plan rates.
API-equivalent cost is not a ChatGPT subscription bill or quota estimate. Missing
usage makes cost unknown. Review records bind to the output hash. Summary tables
are grouped by domain and workflow, and report factual errors and acceptability.
The default quality gate requires 30 distinct reviewed cases overall and 10 per
domain, with no observed unacceptable result or factual error. A passing small
sample is not proof of equal quality; no report automatically enables production.
The manifest binds source implementation, evidence and plan hashes; completion.json
also records workflows left unstarted when the experiment exhausts its global budget.
A small engineering pilot can inform a guarded rollout, but must be reported
separately from passing this representative quality gate.

## Overview charts

The Overview processing section retains historical job-level totals, and adds:

- Per-call daily input/cache/output/reasoning tokens, task and model distributions.
- Reasoning effort, returned/failed/unfinished calls, searches and average duration.
- Actual approved/rejected topic reviews, separately displayed unresolved deferrals.
- Current actionable/deferred/manual backlog, repeated stages and escalations.
- Decisions/hour, measured tokens per completed bounded review, and an estimated
  actionable-backlog drain time at the selected period's rate.

Per-call coverage starts with migration `0008`/v0.0.9. It includes source relevance
and separates relationship work from topic research. Cached input is included in
input, and reasoning is included in output; stacked token segments do not overlap.
A returned JSON response is not a validated decision. Unknown telemetry is shown
explicitly. Current backlog is a snapshot, not invented historical queue data.
