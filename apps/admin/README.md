# DevFeed Admin

Independent Next.js administration UI, using the admin API through same-origin
App Router API routes. It has no direct database, Redis or OIDC-provider access.
The UI sends HSTS and a per-request nonce-based script policy; the internal
Traefik ingress also redirects HTTP to HTTPS.

See [configuration, authentication, service boundaries and commands](../../docs/admin.md).

## Shared table columns

`DataTableColumn` and resource `ColumnSpec` accept a `kind` that selects a shared
`ColumnValue` renderer. Use `name` for names, `slug` for literal monospace slugs,
`boolean` for accessible yes/no indicators, `tags` for lists of badges, `pill` for
colored statuses, and `datetime` for dates using the operator's display settings.
`text`, `number`, `language`, and `label` cover plain values and explicit enum labels.

```tsx
const columns: DataTableColumn<Row>[] = [
  { accessorKey: "name", header: "Name", kind: "name" },
  { accessorKey: "slug", header: "Slug", kind: "slug" },
  { accessorKey: "enabled", header: "Enabled", kind: "boolean" },
  { accessorKey: "tags", header: "Tags", kind: "tags" },
  { accessorKey: "status", header: "Status", kind: "pill" },
  { accessorKey: "created_at", header: "Created", kind: "datetime" },
];
```

Pills infer their semantic color from known statuses; set `tone` to `success`,
`warning`, `info`, `danger`, or `neutral` for other values. Tags accept strings or
objects with a `name`. Missing values share an em dash; false and zero remain
visible. Names and slugs preserve their spelling. A custom `cell` takes precedence
for links, actions, and other specialized content. Individual cell components
are also exported from `components/molecules/column-value.tsx`.

## Source relevance assessments

The **Relevance** tab immediately after **Details** shows the saved assessment separately
from the source's actual approval status. Its decision summary and evidence sit side by
side on desktop. Evidence is paginated in groups of five and can be filtered by classification. The panel explains sample-size, confidence, and classification-count blockers;
uncertain entries count toward the sample total but do not support rejection. Article
titles expand to show their matching feed summary and quoted evidence. Model, version,
and assessment time are under **Assessment details**.

The backend's saved approval/rejection flags are authoritative. The UI explains known
policy versions; it never makes a review decision. Keep its explanatory thresholds in
sync with `packages/core/src/source_relevance.py` when introducing a new policy version.

Validate the panel with `npm run admin:test` and, after `npm run admin:build`,
`node apps/admin/tests/browser/source-relevance.mjs`. The browser check uses a disposable
API fixture and saves desktop/mobile screenshots to `reports/source-relevance/`.

Source policy v4 includes software product management and growth, practical AI product
building, engineering leadership, and careers in software/product roles. Older saved
assessments display a notice that re-analysis is needed under this broader scope. This
source-admission policy is separate from the technical topic taxonomy. Evidence,
confidence, and sample-size requirements are unchanged.

Source policy v5 removes the 80% article requirement for approval: an overall in-scope
assessment with at least 90% confidence, at least three usable sampled entries, and at
least one validated in-scope quote can approve a mixed-content source. Individual
article review is unchanged. Automatic rejection retains its 80% evidence requirement.
The scheduler requeues pending assessments from v1–v4 once, excluding active or failed
jobs; current-version assessments are not repeatedly queued.

## Article eligibility

Classifying an article requires a page-kind decision before approval or publication.
The form preserves an existing decision and defaults missing/legacy decisions to
`uncertain`. Select `non_article` for RSS subscription/feed-link pages, signup forms,
About/contact pages and other site utilities. Technical vocabulary or matching
topics does not establish article eligibility. Actual RSS tutorials remain eligible.
Manual classification API/CLI clients should send `page_kind`; omission records
`uncertain` and blocks approval until the page has been assessed.

Validate the page-kind form with `npm run admin:build` followed by
`node apps/admin/tests/browser/article-page-kind.mjs`. The browser fixture checks
legacy defaults, saved decisions, and desktop/mobile layout.
Screenshots: [desktop](screenshots/article-page-kind-desktop.png),
[mobile](screenshots/article-page-kind-mobile.png).

## Partner tools: private evaluation

Open **Content → Partner tools** to manage the partner inventory. This feature has no
reader endpoint, feed insertion, launch page, notification, or public placement.

1. Add a product manually, or paste a Nick Launches API `results` response (or array)
   using **Import a product collection → Nick Launches API JSON**. Each import accepts
   at most 50 products. Automatic network synchronization is not enabled; API access
   and partner update/withdrawal delivery must be agreed before adding a scheduled importer.
2. Add specific technologies and capability evidence: an official documentation URL,
   exact quote, and the capability it supports. Imported marketing categories and
   popularity never count as verified evidence. Check sources manually.
3. Record a review note and confirm evidence and display permission before approval.
   Approval is optimistic: if the product changed, refresh and review again. Pause,
   reject, or withdraw products explicitly. Verification expires after 90 days.
4. Run a private evaluation against up to 20 published article IDs, including unrelated
   examples, or leave the sample blank for the latest 20 articles. Only tutorials can
   qualify in this pilot. Original publisher summaries provide the article evidence;
   short or incomplete summaries should produce no match rather than inferred claims.
5. Refresh for results. Review both proposed matches and no-match results. **Agree**
   confirms the assessment, including a correct negative; **Disagree** flags it for
   analysis. Neither action publishes anything. The latest 10 runs are shown, with
   all run snapshots and review history retained in the database.

Generic imports use an array of objects with `provider`, `external_id`, `name`,
`product_url`, `listing_url`, `description`, `pricing` (`free`, `freemium`, `paid`,
`unknown`), `technologies`, `attribution`, and `evidence` (objects containing `url`,
`quote`, and `capability`). Provider plus external ID is the stable identity. An
identical reimport is a no-op; material changes invalidate verification. Native Nick
imports preserve local technology/evidence enrichment. Withdrawn, rejected, and paused
products stay in that state on reimport. Missing items are not treated as deletions.

Migration `0021` adds `partner_products` and `partner_evaluations`; run the normal
migration job before updating services. The scheduler dispatches evaluations to the
existing `source-analysis` queue, which requires AI-enabled workers. Jobs use exclusive
leases, at most three attempts, bounded samples, and a product/article snapshot check
before saving results. Changed or expired evidence makes historical results stale.

The model may propose only grounded candidates; deterministic checks reject missing
articles, fabricated article quotes, unsupported technology matches, and non-tutorial
positives. Human review is still required to establish semantic relevance. No real
partner sample has been validated simply by shipping this workflow. Reader placement,
frequency controls, advertising campaigns, billing, and behavioral targeting are not
part of this milestone.
