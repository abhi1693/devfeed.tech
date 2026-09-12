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
