# Autonomous research and publication

Apply migrations through `0001` and recreate application services
before using full mode. The migration adds durable article scheduling fields; it does
not itself approve or publish anything. Enabling full mode starts backlog processing.
Saved source policies remain unchanged and apply outside full mode.

## Automatic tag-to-topic links

Migration `0001` adds background discovery for the existing
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
attempts are not repeatedly researched. Outside full automation, earlier imports
stay available through the existing explicit research action. Disabling the setting
pauses automatic scheduling unless full automation enables it.

### Full application automation

Set `DEVFEED_FULL_AUTOMATION=true` to run the content pipeline and taxonomy through
automated decisions without a manual-review endpoint in the workflow. This mode
enables AI, import research, topic approval, relationship research/approval and
article reanalysis after topic changes, overriding their individual switches.
It requires a configured Codex endpoint, model and account. It also enables exact
tag matching, source admission, article recovery and publication decisions.
Saved per-source policies take effect again when full mode is disabled.

The scheduler also picks up older unresearched proposals and complete drafts.
When research or verification cannot approve a topic, it schedules a correction
using the failed checks as feedback. Correction can replace unsupported metadata
or remove optional aliases, descriptions, URLs and facts; it cannot change the
topic's name or slug. A concept does not need an invented website or logo to pass.
The original draft, corrected draft, source citations, reasons and linked run IDs
remain in proposal evidence. The entire corrected draft must pass a fresh,
independent identity/relevance and citation check before approval.

At most four correction jobs are outstanding. Corrections wait five minutes after
a terminal research/verification result and allow three rounds by default;
`DEVFEED_TOPIC_CORRECTION_MAX_ATTEMPTS` accepts 1–5. Each round retains the existing
bounded inference and verification retries. Provider capacity outages pause work
without consuming those retry budgets. Scheduler and worker restarts resume the
durable jobs automatically.

Out-of-scope or unresolved exact identities are rejected with an attributed reason.
Other unverified drafts are rejected when their correction budget is exhausted.
Terminal relationship verification failures also become automatic rejections.
An inability to verify is recorded as such, rather than claiming the proposal was
proven false. Pending is a processing state in this mode, not a request for human
intervention. Changed inputs receive fresh research; late workers cannot overwrite
edits or completed decisions.

With full automation disabled, individual automation settings retain their existing
behavior: blocked drafts may remain pending for manual review. Queued corrections
pause until full automation is enabled again.

### Growing relationship coverage

Apply migration `0001` to enable automatic relationship
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

Automatic topic approval requires its research citations and a separate review of
the **entire proposed draft** to pass. Imported names, aliases, kind, descriptions,
keywords, URLs and facts are not trusted merely because they were already filled.
Every nonempty field and every alias must receive an explicit verdict supported by
independently fetched citations. Forks, dependencies, broad categories and sibling
products cannot become identity aliases. Outside full automation, incorrect or uncertain drafts remain pending with their
research and verification decisions available for review. Full automation corrects
and reverifies them, or records an automatic rejection.
New GitHub imports start with an unclassified kind; research supplies an evidenced
kind before approval. Existing imports are checked as submitted. Full automation can correct pending
drafts in a separate, audited step; it never silently edits approved topics.

Developer relevance is a separate, required verdict, with primary-source evidence
for a direct connection to software development or computing. Accurate identity,
popularity, a GitHub topic page, or merely using software does not qualify a subject.
For example, a game engine or modding API can qualify; the Yu-Gi-Oh! trading card
franchise does not qualify just because it has video games. Out-of-scope topics remain pending outside full automation and are rejected in
full automation; uncertain relevance receives bounded retries. The research
step also checks scope before filling metadata. Legacy identity-only verdicts cannot
authorize automatic approval; pending proposals are rechecked under the new policy.

Each relationship
requires its own citation and an independent semantic review to pass. Research
receives the full descriptions, aliases and official websites of both endpoints.
The independent review opens cited sources and checks exact identity, a direct
technical association, relation type/direction, scope, and whether the evidence supports
the claim. It rejects generic word matches and social or promotional links. An edge
requiring a platform, version, optional-plugin or test-only qualifier cannot be approved
because the graph cannot represent that scope; a qualified explanation cannot make
the unqualified edge accurate.
Every decision is bound to the proposal content hash; edited or reviewed inputs
cannot be approved by a late result. Outside full mode, a failure leaves the proposal pending with its research intact.
In full mode, terminal failures become attributed rejections. Other verified
relationship proposals may still be approved.

Migration `0001` adds a separate durable verification outbox.
With AI and the corresponding automatic approval policy enabled, the scheduler
backfills pending proposals from completed research, including older runs. It admits
at most 50 metadata and 4 relationship verification jobs at a time. Metadata checks
use the `analysis` AI queue; relationship checks use the fair `relationships` queue.
Both honor Codex readiness and its capacity cooldown. Enrichment is not repeated.
Unchanged metadata is approved only after citation recovery and complete identity
verification; human edits stay pending. A verification-policy upgrade reopens old
terminal verification tasks for pending proposals once, preserving the prior cycle
in the research history and applying a fresh bounded retry budget.

Transport failures, timeouts and retryable HTTP responses receive at most three
verification attempts, with 5- and 10-minute backoff and publisher `Retry-After`
honored up to one day. Old HTTP failures lacking status receive one new check to
classify retryability. Permanent HTTP errors, non-HTML sources and quote mismatches
remain reviewable without repeated fetches. Uncertain semantic checks also have
bounded retries. AI capacity deferrals pause work without exhausting that budget.
Interrupted jobs recover after their five-minute lease expires; duplicate delivery
cannot approve twice. Disabling AI or the policy pauses verification without
losing its jobs. Exhausted retries remain failed for inspection. In full automation, the scheduler
uses terminal results to continue correction or record a rejection automatically.

Research results retain `evidence_verification`, `topic_verification`, `relationship_verification`,
`verification_attempts`, and approval decisions. The `research_verification_jobs`
table records status, attempts, next availability, errors and model usage.
Scheduler logs include `verifications_scheduled`, `verifications_dispatched` and
`verifications_recovered`. A successful check reduces risk; model review still
does not guarantee that every source or relationship is correct.

The confirmed September 10 identity audit corrections are reproducible with
`python scripts/repair_identity_audit.py` in the application environment. This is
a read-only dry run; `--apply` corrects the six audited topics and retracts the
audited WebKit-to-XAMPP dependency in one transaction. Exact before-state checks
reject unexpected edits. Topic corrections create attributed review records and
retain the original automatic approvals; relationship retraction retains the old
review in research history. Repeating the repair skips already corrected records.

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

## Articles and sources in full mode

Validated source submissions are admitted automatically, with an attributed review.
Existing enabled pending sources are admitted in bounded batches. This admits a feed
for ingestion; it does not certify its articles as developer content. Disabled and
rejected sources stay stopped. Feed fetch failures retain their existing bounded
job retries and subsequent scheduled polling. Optional images and source metadata
are not publication requirements.

Approved enabled sources use `full-automation-v1` regardless of their saved manual,
preview or auto policy. Articles still need current evidence, a current catalog,
resolved developer relevance, an active primary topic and all publication checks.
Fresh analysis can complete an edited pending draft; completed editorial decisions
are not reopened. Source tags retain provenance; exact unambiguous topic matches
are linked automatically. Source-provided tags without a known topic create draft
proposals for research and fresh independent verification. Prior rejected proposals
and inactive identities are not recreated. Unmatched or ambiguous tags remain
unlinked until a verified, unambiguous match exists; associations are never guessed.

The scheduler checks at most `DEVFEED_AUTOMATION_BATCH_SIZE` pending articles per
tick, using a durable indexed due time and five-minute spacing per article. This
also resumes the historical pending backlog: page enrichment first, then analysis,
then an audited publication or rejection through the ordinary editorial service.
Changed source evidence, editorial revisions and catalog candidates require fresh
analysis. Active jobs and provider cooldowns are allowed to finish; they are not
rejected merely because the provider is temporarily unavailable.

Empty source text, exhausted analysis retries, unrelated content and unresolved
publication checks end in attributed rejection. A matching pending topic can delay
an article's decision for up to 24 hours from its first automation check. New catalog
candidates trigger fresh analysis during that window. If the topic remains unresolved,
the article is rejected rather than waiting for manual review indefinitely. Rejected
articles are not automatically reopened by later topic approvals. Rejection means
publication eligibility could not be established, not necessarily that content is false.
All decisions retain their reasons, source, input hash and analysis reference.

Turning full mode off pauses this scheduler and restores the individual automation
switches and source publication policies. It does not undo completed decisions.
Notifications still require configured delivery and explicit subscriptions; full mode
does not invent recipients or external sources.

## Publication policies outside full mode

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
