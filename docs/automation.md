# Autonomous research and publication

Apply migration `0008_autonomous_pipeline` and recreate application services before
using these settings. Existing source policies remain **Manual review**; the
migration does not approve sources, publish articles, or enqueue historical work.

## Automatic tag-to-topic links

Migration `0010_tag_topic_discovery` adds background discovery for the existing
single topic link on each tag. `DEVFEED_AUTO_LINK_TAGS=true` (the default) enables
it independently of AI, research approval, and Codex availability. Every normal
scheduler pass (15 seconds by default) checks at most `DEVFEED_AUTOMATION_BATCH_SIZE`
tags, including tags imported from feeds and previously unlinked tags.

A tag's name, slug, and aliases are matched against the names, slugs, and aliases
of active topics. Case, Unicode width, spaces, underscores, and hyphens are
normalized; meaningful punctuation is retained. Exactly one matching topic creates
the link. No matches leave it unlinked; multiple matches are marked **ambiguous**
and remain unlinked. Keywords, substrings, and semantic similarity do not establish
identity. These links appear in the topic's related tags and knowledge graph;
article topic assignments still require their own classification evidence.

New tags and tag identity edits become eligible immediately. Topic identity or
status changes reopen automatic coverage, including previously unmatched tags
and existing automatic links. Disappearing or ambiguous matches clear automatic
links. Indexed identity lookups and persisted revisions let bounded batches resume
after restarts; completed unchanged tags stay idle. Turning the setting off pauses
discovery without losing changes. Scheduler logs include `tags_scanned`,
`tags_linked`, `tags_unlinked`, and `tags_ambiguous`.

Existing nonempty links remain manual during migration. In the tag editor,
**Discover topic automatically** controls each tag. Turn it off to select or clear
the topic manually; turn it on to resume discovery. The API accepts
`auto_link_topic`; explicitly writing `topic_id` without this flag makes the link
manual, including a deliberate clear. Newly created unlinked tags default to
automatic discovery. CLI equivalents are `--auto-link-topic` and
`--no-auto-link-topic`; `tags update --clear-topic` also disables discovery.
An explicit topic deletion/replacement preserves its tag decisions as manual.
The tag list/detail shows pending, matched, unmatched, ambiguous, or manual status
and the last discovery time.

## Research and evidence

```dotenv
DEVFEED_AI_ENABLED=true
DEVFEED_AUTO_RESEARCH_IMPORTS=true
DEVFEED_AUTO_REANALYZE_TOPICS=true
DEVFEED_AUTO_RESEARCH_RELATIONSHIPS=true
DEVFEED_AUTO_APPROVE_TOPICS=true
DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS=true
```

AI still requires a configured Codex endpoint, model and account. Imports explicitly
submitted by an operator, including GitHub curated imports, record a durable research
request when automatic research is enabled. The scheduler claims at most
`DEVFEED_AUTOMATION_BATCH_SIZE` (default 50) per tick. Completed metadata and prior
attempts are not repeatedly researched. Earlier imports stay available through the
existing explicit research action. Disabling the setting pauses automatic scheduling.

### Growing relationship coverage

Apply migration `0009_relationship_coverage` to enable automatic relationship
coverage. With `DEVFEED_AUTO_RESEARCH_RELATIONSHIPS=true`, the scheduler picks up
existing active topics and records a durable scan whenever a topic becomes active
or its name, slug, kind, aliases, description or website changes. Changes are
recorded even while automation is disabled; enabling it resumes the latest scans.
Logo-only edits and relationship approvals do not restart research.

Each topic revision receives an ordered generation. Newer revisions research
older active revisions, with either relationship direction allowed. This assigns
each pair to one scan, including topics added after an earlier job was queued and
the first topic that originally had no peers. A changed peer takes responsibility
for researching its updated relationships. Completed unchanged scans stay idle.

Scans persist a cursor and use at most `DEVFEED_RELATIONSHIP_RESEARCH_BATCH_SIZE`
peers per job (default 100, maximum 500), so catalogs larger than the manual
5,000-candidate limit are processed incrementally. The scheduler admits new
automatic jobs only while the total queued/running relationship count is below
`DEVFEED_RELATIONSHIP_RESEARCH_MAX_PENDING` (default 4). Manual requests and an
existing backlog can exceed this limit. A separate `relationships` queue shares
the AI workers with the `analysis` queue in round-robin order, so bulk metadata
imports do not monopolize processing. Both queues honor Codex readiness and the
shared capacity cooldown. The scheduler runs every 15 seconds; AI queue
load determines when research completes.

Only successfully researched batches advance the cursor. Restarts resume the
saved job/cursor, and stale jobs are replaced using current topic data. After
normal worker retries are exhausted, a failed batch retries after 1, 2, 4, 8, 16,
then 24 hours, with a maximum one-day delay. Full 20-suggestion responses are
followed by another pass over that batch, excluding the new proposals, before
advancing. Existing edges and pending or unchanged reviewed proposals remain
excluded. Relationship evidence verification and approval policy still apply;
research coverage does not mean that every pair has an edge or verified evidence.

The AI analysis history shows these jobs as **Automatic relationship research**.
Scheduler logs report `relationship_jobs_scheduled` and
`relationship_scans_completed`. Disabling the setting pauses new automatic work;
already queued work continues through the normal worker lifecycle.

Creating or changing the identity, aliases, kind or keywords of an active topic
records a resumable scan in the same transaction. Both previous and current
names, aliases and keywords identify affected articles. The scheduler examines at most
50 pending, unpublished articles per tick for the oldest scan. Matching articles
with approved sources and enough text are queued for analysis. Approved and
rejected articles are excluded. Description, logo and graph-edge edits do not create
scans. A rollback also rolls back its scan. New articles use the current catalog
through ordinary ingestion and enrichment.

Analysis deduplication includes the selected catalog hash as well as source content
and prompt version. A catalog change during inference supersedes the old result
and queues a replacement when editorial state still permits it. Manual decisions
remain authoritative; unchanged inputs are not repeatedly analyzed.

Research citations are fetched independently through the existing guarded public
HTTP transport. DNS checks, redirect checks, byte limits, and HTML content-type
checks apply. The entire verification pass has a 45-second budget, configurable
with `DEVFEED_EVIDENCE_TIMEOUT_SECONDS`. Visible quoted text is compared after Unicode
and whitespace normalization; scripts, styles, templates and explicitly hidden
content are excluded. No JavaScript runs. The research run retains the citation,
final URL, retrieval time, content hash and verification outcome, without storing
the downloaded page body.

Automatic topic approval requires its research citations to pass. Each relationship
requires its own citation to pass. A failure leaves the proposal pending with its
research intact, and other verified relationship proposals may still be approved.
Non-HTML sources, inaccessible pages, mismatches and exhausted verification budgets
require review. A quote match establishes that the source contains the cited text;
it does not establish the truth of that text or that a paraphrase follows from it.

## AI capacity and usage

Article candidate retrieval defaults to at most 80 topics and 80 tags, including
at most eight candidates without lexical matches in each set. Settings are
`DEVFEED_ANALYSIS_MAX_CANDIDATES` and `DEVFEED_ANALYSIS_FALLBACK_CANDIDATES`.
The independent 240 KB prompt budget and evidence validation remain in force.
Smaller shortlists reduce prompt size; tune them against representative articles
if relevant subjects are missed. No live model-quality improvement is implied.

The client records cumulative token usage from `thread/tokenUsage/updated`, taking
the latest totals instead of summing repeated updates. Job history retains aggregate
usage, elapsed attempt time and the last 20 recorded attempts. Usage can be absent
when the provider omits events or a worker is killed; these values are activity
measurements, not a billing statement. The dashboard counts reported usage for jobs
finished in the selected period.

Structured usage-limit and HTTP 429 errors set a Redis cooldown shared by workers
using the same Codex endpoint. A reported future reset extends the default
`DEVFEED_AI_CAPACITY_COOLDOWN_SECONDS=300`; another worker cannot shorten it. During
the cooldown, analysis remains queued while ingestion continues. Capacity deferrals
do not exhaust the normal three-attempt transient-error budget. Provider messages
and credentials are never stored as pause reasons. The protocol follows the
[official app-server events and errors](https://learn.chatgpt.com/docs/app-server).

## Publication policies

Open an approved source's details and set **Automatic publication** to
**Preview automatic decisions**. Review decisions in the overview and the article's
History tab. The source can then be switched to **Publish eligible articles
automatically**. Policies are configured per source, with revision checks and
an attributed change history; source submission and ordinary source edits cannot
grant publication authority.

The versioned `trusted-source-v1` policy requires a current successfully applied
analysis using the current catalog, an active primary topic, resolved developer
relevance, supported language
and content metadata, a meaningful original summary, an approved enabled source,
and no model uncertainty reasons. Articles with prior human editorial actions
require review. Relevance scores are not treated as calibrated confidence.

Preview writes an idempotent decision record without approving or publishing.
Automatic mode uses the same checks and the ordinary editorial decision service
to approve and publish atomically. Both review records retain the policy version,
source-policy revision, article revision, input hash and analysis ID. Conflicts and
failed checks leave the article unpublished. Previous preview decisions remain
available after policy changes. Existing articles can be evaluated explicitly from
the overview; changing a source policy does not publish a historical backlog.

## Overview and recovery

The overview groups current articles by insufficient text, missing primary topic,
failed analysis, publication preview and remaining editorial review. Unverified
topic and relationship research is grouped separately. Counts cover all matching
records; each group links to five oldest examples, and groups can overlap.
Recovery acts on one selected article, rechecks its revision and editorial state,
and coalesces active work. It never starts a worker or bypasses publication policy.

Publication metrics use first publication time. The autonomous percentage counts
articles first published automatically without earlier human editorial decisions;
its denominator is all first publications in the selected period. The latency
metric is the median from discovery to first publication. Empty denominators are
shown as unavailable, not as a misleading success rate.
