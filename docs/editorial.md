# Editorial publication, topics, and AI analysis

Articles are candidates after ingestion, not automatically public. The user API
serves only approved, published articles with at least one approved source. Source
approval and article approval are separate decisions. The CLI is the trusted
operator interface; no account system or public moderation endpoints are added.

## Schema cutover

The pre-release migration chain has been replaced by `0001_initial`, which creates
the baseline schema in an empty database. Existing development databases
on the removed chain require your explicit backup/reset/recreation first. No
legacy article/source migration is performed. New articles are pending/unpublished
until they are classified, approved, and published. See the
[migration workflow](../migrations/README.md).

The new code requires this schema. With an older database, `/health/ready` returns
503 `migration_required`; schema-dependent reads return a sanitized 503.
`/health/live` remains a process-liveness check. When ready, stop your processes,
apply `uv run devfeed db upgrade`, and run compatible API, scheduler and worker
versions yourself. Downgrading the baseline drops its tables and their data; it
is not an upgrade compatibility path. Nothing performs a downgrade automatically.

## Pipeline and publication policy

```text
validated source → ingestion candidate → original-page evidence
                 → analysis → operator review or source policy → publication
```

Publisher RSS fields retain priority; page extraction fills missing publisher
fields and enriches aggregator links. Readable text (up to 60,000 characters) is
stored privately for analysis, never exposed through public article responses.
Only IDs travel through RQ. Analysis jobs retain bounded input/catalog snapshots,
prompt version, model, source hash, editorial revision, result and outcome.

Article analysis supports catalogs larger than 500 topics or tags. It ranks active
topics and tags against the article's title, summary, and text using names, aliases,
and keywords, then supplies up to 80 candidates of each kind by default within the 240 KB
prompt budget, including up to eight candidates without lexical matches. The
limit is configurable up to 500. Classification requires verbatim source evidence;
publication uses administrator review or an opted-in source policy. Validation and manual
classification use the complete active catalog; an ID outside the supplied
shortlist cannot be returned by the model. Keywords never activate a topic.

Publication requires:

- A valid public canonical URL and nonempty title.
- Resolved language, content type and content format.
- A meaningful source summary or `ai_summary` (at least 40 alphabetic characters).
- At least one active primary topic and resolved developer relevance.
- An approved source and article approval through editorial review or source policy.

Images, author and publication date are optional; missing values are not invented.
Categories/tags may be empty when no precise match exists. These are application
policy choices, not claims that model classifications are infallible.

`approve` does not publish. `unpublish` preserves approval but hides the article;
`reject` hides it and requires a reason. Decisions increment an editorial revision
and append history. `published_to_feed_at` records first publication in DevFeed,
separately from publisher `published_at`. Source-content changes invalidate
approval and AI prose. A rejection is never reversed by a worker. Reanalysis
requires fresh approval, including for previously published articles. Existing
sources use manual review. Approved sources can opt into preview and then
automatic publication through the [publication policy workflow](automation.md).

## Source data versus generated data

| Fields | Ownership |
| --- | --- |
| Title, summary, author, date, image | RSS/page evidence; not AI-rewritten |
| `ai_summary`, `ai_description` | Generated prose, returned separately |
| Language, content type/format, topic/tag assignments | Validated AI or explicit operator classification |
| Topic description, logo, website, cited facts | Operator/source-backed profile |
| Topic `ai_description` | Reserved separate generated-prose field |

Content type describes intent (tutorial, news, release, comparison, opinion, or
article); format distinguishes article, podcast, video, paper and discussion.
Classification provenance and analysis records are operator-only. AI assignments
replace previous automated/keyword assignments while preserving manual ones.
Legacy keyword/language maintenance cannot overwrite reviewed classification.

## Topics and tags

See [topic management](topics.md) for supervised JSON/CSV imports,
evidence-backed keyword enrichment and individual proposal review.

A topic has a canonical name, unique slug, aliases and a freely chosen `kind`.
Languages, frameworks, models, vendors, practices and infrastructure concepts can
all be subjects. Identities live in the database, not an in-code list. Overlapping
names/slugs/aliases require explicit operator resolution.

Directed relations include `uses_language`, `depends_on`, `implements`, `part_of`
and `related_to`. Relations describe identity/context, **not feed membership**.
Article assignments separately carry primary/supporting/comparison/incidental
roles, relevance scores, evidence excerpts and origins.

`GET /v1/feed?topic=javascript` matches direct primary/supporting JavaScript
assignments. A React–JavaScript relationship alone cannot include an article.
An Angular article does not enter the React feed through JavaScript. Comparison
and incidental mentions are excluded from topic feed membership. Scores are model
relevance estimates, not calibrated probabilities or correctness guarantees.

Broad disciplines and specific technologies share the topic catalog. Tags remain
secondary facets and can link to a topic without creating article assignments.
Topic aliases are alternative names for the same subject; matching keywords are
separate reviewed terms for fallback classification.

Example `react-topic.json`:

```json
{
  "name": "React",
  "slug": "react",
  "kind": "ui-library",
  "aliases": ["React.js", "ReactJS"],
  "website_url": "https://react.dev",
  "facts": []
}
```

```sh
uv run devfeed topics add --file react-topic.json
uv run devfeed topics update TOPIC_UUID --file react-topic.json
uv run devfeed topics relate REACT_UUID JAVASCRIPT_UUID --relation uses_language
uv run devfeed tags update TAG_UUID --topic-id TOPIC_UUID
uv run devfeed topics list
```

Profile update replaces writable fields; include values you want to retain. Facts
have `name`, `value`, `source_url`, and timezone-aware `retrieved_at`. Use verified
release/founding information; omit unknowns. Automatic topic research, fact refresh,
and release tracking are **not implemented yet**. Storing cited facts does not
make them automatically current.

Profiles are exposed through cached GETs at `/v1/topics`, `/v1/topics/{slug}` and
`/v1/topics/{slug}/relations`.

## Codex connection and analysis worker

AI is off by default. For a bundled server, dedicated analysis client and persistent
sign-in, use [Codex in Compose](compose.md#codex-server-and-analysis-client). That
setup uses a private socket bridge and the `ai` profile.

For an external server, set these explicitly when your existing analysis service is
ready. Database and Redis retain their existing connection URLs:

```dotenv
DEVFEED_AI_ENABLED=true
DEVFEED_CODEX_APP_SERVER_URL=ws://127.0.0.1:4500
DEVFEED_CODEX_MODEL=YOUR_CHOSEN_MODEL
# Required for remote wss connections behind your authenticated gateway:
# DEVFEED_CODEX_AUTH_TOKEN=...
DEVFEED_CODEX_TIMEOUT_SECONDS=90
```

The endpoint is illustrative, not a configured default. Remote connections require
`wss` and an explicit bearer token; loopback `ws` is allowed. DevFeed never launches
Codex from an analysis job or reuses another application's credentials. Use a dedicated isolated server
without repository mounts, MCP tools, plugins or unrelated secrets. Your gateway
must validate the bearer token; supplying one does not authenticate a raw listener.

The adapter targets Codex CLI 0.153.4's [app-server protocol](https://learn.chatgpt.com/docs/app-server):
ephemeral threads, `outputSchema`, final completed messages and cancellation. It
creates a fresh named permissions profile denying all filesystem reads and network
access, verifies the server selected it, and starts the turn with those permissions.
Repository instruction discovery is disabled. Shell/web/MCP features and approvals
are disabled, and unexpected tool/interactive requests abort analysis. Older servers
that do not confirm the profile are rejected. WebSocket support is experimental;
verify compatibility and permissions when changing the pinned server version.

Outside Compose, start an analysis worker separately from ingestion:

```sh
uv run devfeed worker --queue analysis
```

Each RQ worker runs one analysis at a time; start with one. Worker replicas bound
concurrency. Structured provider capacity errors establish a shared cooldown across
analysis workers; ingestion continues during the pause. The scheduler dispatches
this queue only when
AI is enabled. Claims wait for dispatcher locks; leases and bounded retries recover
deliveries. Inference holds no database transaction. Output must pass strict JSON,
known-ID and verbatim-evidence checks; these cannot prove semantic accuracy.

Stale source hashes/editorial revisions discard results. New source content
coalesced into an active job receives a follow-up after a superseded result.
Retries preserve editorial decisions. Successfully applied results are evaluated
against the source publication policy. Three ordinary failed attempts require
operator retry; capacity deferrals do not consume that budget. Each prompt uses
a ranked shortlist of 80 topics and 80 tags by default within the 250 KB input
budget; validation uses the full active catalog.
Unknown subjects stay unassigned. Article analysis
cannot create topics or propose new ones; use GitHub discovery or add topics manually.

## Operator workflow

```sh
uv run devfeed articles list --review-status pending --limit 25
uv run devfeed articles inspect ARTICLE_UUID
uv run devfeed articles enrich ARTICLE_UUID --force
uv run devfeed articles analyze ARTICLE_UUID --force
uv run devfeed articles analysis-backfill --limit 100 --dispatch
uv run devfeed articles analysis-backfill --limit 100 --dispatch --force
uv run devfeed articles analyses --article-id ARTICLE_UUID
uv run devfeed articles analysis-dispatch ANALYSIS_JOB_UUID
uv run devfeed articles analysis-retry FAILED_ANALYSIS_JOB_UUID --force
# Reanalyze after manually adding a topic or approving a GitHub proposal.
uv run devfeed articles analyze ARTICLE_UUID --force
uv run devfeed articles approve ARTICLE_UUID --by Operator --revision 0
uv run devfeed articles publish ARTICLE_UUID --dry-run
uv run devfeed articles publish ARTICLE_UUID --by Operator
uv run devfeed articles unpublish ARTICLE_UUID --note 'Needs another review'
uv run devfeed articles reject ARTICLE_UUID --reason 'Not developer relevant'
uv run devfeed articles review-history ARTICLE_UUID
uv run devfeed status
```

Use the actual revision from `inspect`, not the illustrative `0`. On single-article
commands, `--force` dispatches immediately. On `analysis-backfill`, it allows a new
attempt for previously analyzed input; use `--dispatch` to dispatch the batch
immediately. Neither bypasses approval, validation, active-job coalescing, or
rejection. Routine output omits private text and credentials.
Job progress and private classification writes do not invalidate user caches;
publishing/unpublishing and visible changes do.

The existing cache invalidator is best-effort after database commit. If Redis is
unreachable during invalidation but later returns with old keys, cached public
responses can remain stale until their configured TTL. Strict cross-service
instant takedown during cache outages requires a durable visibility fence; this
initial implementation does not claim that guarantee. Operational routes now require an admin session on the separate admin API;
keep that service private. See [administration](admin.md).

Analysis backfill scans pending, unpublished candidates from approved sources in
bounded UUID order. Continue with `--after` using `next_after`; sparse evidence
and previously attempted input are skipped. `--force` reruns previously attempted
input in new job records, preserving existing results and attempt history. It
still skips active jobs, sparse evidence, and articles outside the pending,
unpublished scope. It never approves or publishes a batch.

For manual classification/corrections, use
`devfeed articles classify ARTICLE_UUID --file classification.json`. It replaces
all assignments with manual ownership, invalidates approval, and preserves source
and AI prose. Rejected articles stay rejected. Example (replace UUID, revision and
evidence with actual catalog/input values):

```json
{
  "developer_relevance": "relevant",
  "language": "en",
  "content_type": "tutorial",
  "content_format": "article",
  "topics": [{
    "topic_id": "00000000-0000-0000-0000-000000000001",
    "role": "primary",
    "relevance": 1.0,
    "evidence": "React"
  }],
  "tags": [],
  "actor": "Operator",
  "expected_revision": 0
}
```

Tag selections use `id` and `evidence`. Extra fields, including attempts
to overwrite source/AI prose through this command, are rejected.
