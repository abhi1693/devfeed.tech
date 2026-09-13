# Recurring model benchmarks

Use this workflow before changing a model, reasoning effort, prompt, candidate
selection, or inference transport. Repeat monthly for the current baseline and
allowlisted candidates, and after adding a use case or observing a production
failure. The runner is local and independent of application databases. It never
publishes results or changes production routing. Paid runs require `--execute`;
ordinary CI exercises the harness without making model calls.

## Prepare a versioned dataset

```sh
uv run python scripts/model_benchmark.py prepare-articles \
  --samples reports/private-samples.json --out reports/benchmarks/cases-v1.json
```

The input is a JSON array of `input_snapshot` and `catalog_snapshot` objects from
an explicitly authorized read-only export. This freezes the actual article prompt,
schema, source text, and candidate identities. No fresh fetch occurs during an
article benchmark. Keep private snapshots and results under ignored `reports/`,
not Git. Review exported data before using it; never include credentials.

Curate each case's `domain`, `language`, `difficulty`, and `split` (`development`
or `holdout`). Use production-representative strata: databases, security, AI/ML,
frontend, backend, infrastructure, and other domains actually present. Include
short/long text, ambiguous topics, irrelevant content, insufficient evidence,
non-English content, and known failures. Keep related/duplicate articles in the
same split. Do not tune prompts against the held-out set. Start with at least 30
independent reviewed cases per task/domain; increase this for consequential or
rare failure modes. Repeats measure variability, not additional independent cases.

For any new task, add a case with the same envelope:

```json
{
  "id": "relevance-database-001",
  "task": "source_relevance",
  "domain": "databases",
  "language": "en",
  "difficulty": "ambiguous",
  "split": "holdout",
  "prompt": "The exact frozen task prompt and evidence go here",
  "schema": {"type": "object"},
  "validator": "json",
  "expected": {"relevance": "relevant"},
  "label_provenance": "Human review record and rubric revision"
}
```

Use the real production schema, not the abbreviated example. `expected` maps
object paths to exact human-reviewed values. A baseline model output is not ground
truth. For summaries and research, use the human rubric instead of string equality.
The `article` validator runs DevFeed's actual schema and quote/identity checks.
Generic `json` tasks send their schema to the CLI but currently have no independent
local schema validator: their automatic checks cover supplied gold fields only.
Add a task-specific validator and regression fixtures when stronger automated
checks are needed. Research with live search/tool use requires a separate transport
adapter and timestamped tool evidence; this runner intentionally measures frozen
text inputs with tools disabled. Do not infer live research competence from it.

## Run the same comparison repeatedly

```sh
# Prints the exact call count and fingerprint without invoking a model.
uv run python scripts/model_benchmark.py run --plan benchmarks/models.json \
  --cases reports/benchmarks/cases-v1.json --out reports/benchmarks/2026-10-v1

# Run only after checking the plan, rates and call count.
uv run python scripts/model_benchmark.py run --plan benchmarks/models.json \
  --cases reports/benchmarks/cases-v1.json --out reports/benchmarks/2026-10-v1 --execute
```

The checked-in plan compares Terra medium with Luna low, with two repeats and a
200-call ceiling. GPT-5.5 is excluded. Refresh the dated price table against the
linked official pricing page before each campaign; rates above the configured
Terra ceiling are rejected. A call ceiling bounds requests, not dollars or tokens.
There is no automatic retry, fallback, model discovery, or scheduled paid invocation.

Order is shuffled reproducibly to reduce systematic cache/order effects. Calls
are sequential to avoid perturbing local workloads. The manifest freezes dataset,
plan, prompt/schema content, seed, CLI version, Git SHA and dirty status. Resume
rejects changed inputs/configuration. Completed and interrupted calls are not
silently repeated; use a new run directory for an intentional repeat. Each attempt
is registered before execution. Timeouts, failures and missing usage remain visible.

## Review and decide per task/domain

`REPORT.md` and `summary.json` include task, domain, task/domain, language,
difficulty and split breakdowns, failures, latency, token counts and known API cost.
The costs are short-context estimates using observed cache hits, not subscription
bills. Missing usage is unknown, and reasoning tokens are already in output tokens.
Do not attribute differing cache hit rates entirely to model or prompt improvements.

Share only `review-queue.json` with reviewers to hide model names and randomize
presentation. Reviewers judge factuality, supported claims, correct identities,
relevance, completeness and appropriate abstention. A material error fails. Save
one review per opaque ID under `RUN/reviews/ID.json`:

```json
{"reviewer": "reviewer-id", "pass": false, "notes": "Unsupported topic assignment"}
```

```sh
uv run python scripts/model_benchmark.py report --out reports/benchmarks/2026-10-v1
```

The report pairs candidate and baseline reviews on identical cases and repeats.
It identifies regressions and improvements for each task/domain. Incomplete review,
small samples, or development-only data remain insufficient evidence. A fully
reviewed held-out slice with no observed regression is eligible for further
validation, never automatically promoted. This is a screening rule, not a statistical
proof of equivalence. Review failure severity, disagreement and confidence bounds
before changing routing. Do not average an unsafe domain into a good overall score.

Retain dated run directories to compare repeated campaigns on the same dataset
fingerprint. When adding failures, create a new dataset version and rerun baseline
and candidates together. Document the decision and scope: keep baseline, reject,
collect more evidence, or propose a limited task/domain rollout with rollback.
Production token accounting then checks whether realized costs match the experiment.

The initial 50-article pilot is useful regression material but has no independent
human labels or curated domain coverage. It cannot authorize a universal model
replacement. Broader use cases need their own curated cases and validators.

This workflow follows the task-specific datasets, continuous evaluation and human
calibration described in [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
