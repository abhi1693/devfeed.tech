# DevFeed Admin

Independent Next.js administration UI, using the admin API through same-origin
App Router API routes. It has no direct database, Redis or OIDC-provider access.

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
