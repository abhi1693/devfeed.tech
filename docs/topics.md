# Topic management

Topics are DevFeed's single subject catalog. Broad subjects such as Databases and
specific technologies such as PostgreSQL live together, distinguished by `kind`.
Aliases identify the same subject. Matching keywords are separate terms used by
fallback classification. Tags remain secondary facets; content type and format
remain separate fields.

## Find and add topics

Open **Topics → Discover and review → Article analysis** to collect suggestions
from the latest 100 successful, applied analyses. New analyses also submit suggestions
automatically. Existing identities and previously reviewed proposals are deduplicated.
Discovery only creates pending proposals. Review their source quotes and article
links, edit the fields, then approve or reject each proposal.

For a deliberate manual addition, use **Topics → Add topic**. For a catalog, use
**Topics → Import**, upload or paste JSON/CSV, preview, then send proposals for review.
The [example catalog](examples/topics.json) is editable input, never an automatic seed.

```json
[
  {"name":"Databases","slug":"databases","kind":"discipline","keywords":["database design"]},
  {"name":"PostgreSQL","slug":"postgresql","kind":"database","aliases":["postgres"]}
]
```

Import limits are 100 rows and 256 KiB. Required fields are `name`, `slug`, and `kind`.
Optional fields are `description`, `aliases`, `keywords`, `website_url`, `logo_url`,
and source-attributed `facts`. CSV aliases and keywords use `|`; facts use a JSON array.
Omitted optional fields preserve existing values; explicit nulls clear nullable fields.
Duplicate JSON keys, duplicate CSV headers, unknown fields, identity collisions and
pending proposals for the same slug block submission. Status and actor fields cannot
be supplied by an import. A changed input or catalog requires another preview.

## Discover from GitHub

Open **Topics → Discover and review**, choose **GitHub curated topics**, and click
**Pull from GitHub**. No search terms, kind selection, repository configuration, or
token are required. The source is the community-curated
[github/explore](https://github.com/github/explore) repository.

The import reads the repository at a fixed commit and automatically processes all
its topics in batches. Existing topic identities and previously submitted proposals,
including rejected suggestions, are skipped. New topics become pending proposals
with GitHub source links. Nothing becomes active until you approve it. You can edit
the proposed fields during review. GitHub has no `kind` field, so new suggestions
start as `technology`; aliases remain identity aliases, never matching keywords.

Progress shows how many topics were checked and added. If a request fails, choose
**Continue pulling**; completed batches remain available for review. Starting again
also skips existing proposals. Invalid metadata is reported without stopping valid
rows. The repository archive and documents are bounded and never extracted or run.

## Enrich and review

**Operations → AI analysis** lists both article and topic proposal runs. Filter by
type or status, or search by subject name or run ID. Open a run for its details and
runtime logs; open its subject to review the proposal or article. The list and run
details refresh every five seconds while the page is visible. Topic enrichment still
requires a separate approval before changing the active topic catalog.

On the proposal table, search names, slugs, descriptions, aliases, keywords, or sources.
Use the AI status selector to find proposals not yet analyzed, queued, running,
ready for review, without additions, or failed. It always reflects the latest run.
The **Filters** menu adds source, topic kind, new/update change type, and missing
information (any field or a specific field). Source and kind choices come from the
whole proposal catalog. Filters apply before pagination; active chips can be removed
individually or cleared together. Filters stay in the URL across status tabs, sorting,
and pagination. **Analyze all pending** continues to cover all pending proposals,
regardless of the table filters.

Open a topic and choose **Enrich keywords**. Suggestions use existing tags on at least
two approved, published articles directly assigned that topic as primary/supporting.
The latest 200 articles provide evidence for up to 25 new terms. Select useful terms
and submit them for review. An empty evidence set adds nothing.

The proposal page shows one editable form with evidence, timestamps,
and submitting/reviewing identities. After review, the form is read-only and shows
the applied values for approvals or the proposed values for rejections.
Approval applies the reviewed fields; rejection
leaves the catalog unchanged. Concurrent reviews and stale target snapshots are rejected.
Sourced facts are retained and shown during review; use the topic editor to edit facts.

Topic approval does not assign articles or publish them. To reanalyse eligible pending
articles after approving topics:

```sh
docker compose exec api devfeed articles analysis-backfill --limit 100 --dispatch --force
```

Relationships such as `part_of` describe context. A PostgreSQL–Databases relationship
does not implicitly add PostgreSQL articles to the Databases feed. Assign each relevant
subject directly. Primary/supporting roles determine topic feed membership; comparison
and incidental mentions do not.

## Private API

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/admin/topic-imports/preview` | Validate `{format, content, source_name}` without writing |
| `POST /v1/admin/topic-imports` | Submit input plus `preview_token` as pending proposals |
| `POST /v1/admin/topic-discovery` | Discover pending proposals from recent analysis results |
| `POST /v1/admin/topic-discovery/github` | Pull a batch; the UI continues automatically using the returned revision and offset |
| `GET /v1/admin/topic-proposals` | Review inbox, filtered by status or batch |
| `GET /v1/admin/topic-proposals/{id}` | Fields, evidence and review history |
| `POST /v1/admin/topic-proposals/{id}/review` | Approve with reviewed `topic` fields, or reject; optional note |
| `POST /v1/admin/topics/{id}/enrichment/preview` | Preview keyword suggestions |
| `POST /v1/admin/topics/{id}/enrichment` | Submit selected `keywords` plus `preview_token` |

All endpoints require admin authentication and browser writes require CSRF protection.

## Existing databases

Stop applications, back up PostgreSQL, apply `0004_topics_ssot`, then start updated
images together. `python3 scripts/compose_dev.py --ai` coordinates builds and migration.
Pause native Compose watch during this schema change; resume it after the upgrade.

The migration merges unambiguous categories into topics, preserves direct and tag-derived
article assignments, converts parent links into `part_of` relationships and transfers
proposals. Original category, tag, assignment and proposal rows remain in
`taxonomy_migration_archive` for audit, including the original topic assignments.
Before changing the schema, the migration checks that consolidation will stay within
the classifier's 500 active-topic limit. Oversized catalogs must be reconciled before
retrying; the migration reports the combined count and leaves legacy data intact.
Overlapping category/tag assignments retain manual provenance and primary/supporting
membership. Existing manual comparison/incidental decisions that conflict with legacy
membership, ambiguous identities, or conflicting pending proposals abort the transaction
for reconciliation. Categories have no runtime table,
API, CLI group or admin screen after the migration. Historical AI JSON/logs remain intact.
This migration is forward-only; rollback requires the pre-migration backup.


## AI research for pending proposals

In **Topics → Proposals**, use the sparkle button on a pending row, or open its
review page and choose **Run AI analysis**. **Analyze all pending** queues every
pending proposal with missing fields across all pages and filters. Proposals with
active jobs or complete metadata are skipped, and the result reports each count.
With notifications enabled, research failures, retries, and completed enrichments
also appear in the admin inbox with a link to the topic analysis run.
The jobs continue in the background after you leave the page. The page shows queued/running status
and refreshes when research finishes. Resolve any unsaved edits before starting.

Research fills empty descriptions, aliases, keywords, official website/logo URLs,
and sourced facts. Existing fields and topic identity remain unchanged. Codex may
search the public web and consult primary sources; shell, local file access and
connectors remain disabled. The Code Mode host remains available to dispatch web tools
for models that require it; it does not enable shell tools. Each addition must include source attribution, displayed
on the review page. These are AI-supplied citations for the administrator to check.
Uncertain information stays empty and the run explains missing evidence.

The proposal remains pending. Review/edit the result, remove unsupported facts,
and explicitly approve or reject it. A review of an older version returns a conflict
so it cannot accidentally overwrite an AI update.

Start the AI services and sign in as described in [Compose](compose.md#codex-server-and-analysis-client).
`POST /v1/admin/topic-proposals/analysis` queues all pending proposals;
`POST /v1/admin/topic-proposals/{id}/analysis` queues one. Both require an admin session and CSRF,
return 202 and deduplicate concurrent requests for the same proposal. The bulk
endpoint atomically creates durable runs and returns queued/active/complete counts. RQ processes these jobs on the existing `analysis` queue. Interrupted
jobs recover through leases, with at most three attempts. Reviewed or changed
proposals discard late results. Run records and logs are available under
`/v1/admin/jobs/topic-analysis/{job_id}` and its `/logs` endpoint.

Migration `0005_topic_analysis` adds the job table. For this update, run
`python3 scripts/compose_dev.py --ai` before restarting native Compose Watch.
