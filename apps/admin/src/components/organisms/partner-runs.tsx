"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { PageHeading } from "@/components/molecules/page-heading";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { Button } from "@/components/atoms/button";
import { useRequest } from "@/lib/use-request";
import { useTableQuery } from "@/lib/use-table-query";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { adminPartnerPipelineList, adminPartnerEvaluationsList } from "@/lib/api/generated/admin";
import {
  partnerRunLabels,
  partnerRunHref,
  partnerRunPath,
  partnerProductHref,
  partnerOperation,
  type PartnerRunKind,
} from "@/lib/partner-runs";
import { partnerHref, partnershipTrail } from "./partner-connections";

type Run = {
  id: string;
  status: string;
  product_id: string | null;
  product_name: string | null;
  created_at: string;
  finished_at: string | null;
  attempts: number;
  error: string | null;
  provider?: string;
  operation?: string;
  external_id?: string | null;
  matches?: number;
  articles?: number;
};
type RunPage = { items: Run[]; total: number; offset: number; limit: number };

export function PartnerRuns({ kind }: { kind: PartnerRunKind }) {
  const query = useTableQuery(`partner-${kind}`).toString();
  const router = useRouter();
  return (
    <section className="space-y-6">
      <PageHeading title={partnerRunLabels[kind]} trail={partnershipTrail} />
      <RunTable
        kind={kind}
        query={query}
        onQuery={(next) => router.replace(`${partnerRunPath(kind)}?${next}`, { scroll: false })}
      />
    </section>
  );
}

export function RelatedPartnerRuns({
  kind,
  filters,
  title,
}: {
  kind: PartnerRunKind;
  filters: Record<string, string>;
  title?: string;
}) {
  const [query, setQuery] = useState("");
  const params = new URLSearchParams(query);
  Object.entries(filters).forEach(([key, value]) => params.set(key, value));
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-4">
        <h2 className="font-semibold">{title ?? partnerRunLabels[kind]}</h2>
        <Link
          prefetch={false}
          href={`${partnerRunPath(kind)}?${new URLSearchParams(filters)}`}
          className="text-xs text-blue-700 dark:text-blue-400 hover:underline"
        >
          View full list
        </Link>
      </div>
      <RunTable kind={kind} query={params.toString()} onQuery={setQuery} embedded />
    </section>
  );
}

function RunTable({
  kind,
  query,
  onQuery,
  embedded = false,
}: {
  kind: PartnerRunKind;
  query: string;
  onQuery: (next: string) => void;
  embedded?: boolean;
}) {
  const params = new URLSearchParams(query);
  const bounded = (value: string | null, fallback: number, max: number) =>
    value !== null && /^\d+$/.test(value) ? Math.min(max, Number(value)) : fallback;
  const offset = bounded(params.get("offset"), 0, 100000000);
  const limit = Math.max(1, bounded(params.get("limit"), 25, 100));
  const sort = params.get("sort") ?? "-created_at";
  const status = params.get("status") ?? "";
  const operation = params.get("operation") ?? "";
  const q = params.get("q") ?? "";
  const provider = params.get("provider") || undefined;
  const productId = params.get("product_id") || undefined;
  const parentId = params.get("parent_id") || undefined;
  const [revision, setRevision] = useState(0);
  const interval = useRefreshInterval();
  function change(values: Record<string, string>) {
    const next = new URLSearchParams(query);
    next.set("offset", "0");
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    onQuery(next.toString());
  }
  const load = useCallback(
    async (signal: AbortSignal): Promise<RunPage> => {
      const validStatus = ["queued", "running", "succeeded", "failed"].includes(status)
        ? (status as "queued" | "running" | "succeeded" | "failed")
        : undefined;
      const common = {
        q,
        status: validStatus,
        sort,
        offset,
        limit,
        provider,
        product_id: productId,
      };
      return kind === "pipeline"
        ? adminPartnerPipelineList(
            {
              ...common,
              parent_id: parentId,
              operation: ["sync", "sync_product", "assess"].includes(operation)
                ? (operation as "sync" | "sync_product" | "assess")
                : undefined,
            },
            { signal },
          )
        : adminPartnerEvaluationsList(common, { signal });
    },
    [kind, q, status, sort, offset, limit, provider, productId, parentId, operation],
  );
  const result = useRequest(`partner-${kind}/${query}/${revision}`, load, interval * 1000);
  const columns: DataTableColumn<Run>[] = [
    {
      id: "id",
      header: "Run",
      accessorKey: "id",
      cell: ({ row }) => (
        <Link
          prefetch={false}
          href={partnerRunHref(kind, row.original.id)}
          className="font-medium text-primary hover:underline"
        >
          {row.original.id.slice(0, 8)}
        </Link>
      ),
    },
    ...(kind === "pipeline"
      ? [
          {
            id: "operation",
            header: "Operation",
            accessorFn: (row: Run) => partnerOperation(row.operation ?? ""),
          },
          {
            id: "provider",
            header: "Partner",
            cell: ({ row }: { row: { original: Run } }) =>
              row.original.provider ? (
                <Link
                  prefetch={false}
                  href={partnerHref(row.original.provider)}
                  className="text-primary hover:underline"
                >
                  {row.original.provider}
                </Link>
              ) : (
                "—"
              ),
          },
        ]
      : []),
    {
      id: "product",
      header: "Product",
      cell: ({ row }) =>
        row.original.product_id ? (
          <Link
            prefetch={false}
            href={partnerProductHref(row.original.product_id)}
            className="text-primary hover:underline"
          >
            {row.original.product_name ??
              row.original.external_id ??
              row.original.product_id.slice(0, 8)}
          </Link>
        ) : (
          (row.original.external_id ?? "—")
        ),
    },
    { id: "status", header: "Status", accessorKey: "status", kind: "pill", enableSorting: true },
    {
      id: "attempts",
      header: "Attempts",
      accessorKey: "attempts",
      kind: "number",
      enableSorting: true,
    },
    ...(kind === "evaluations"
      ? [{ id: "matches", header: "Matches", accessorKey: "matches", kind: "number" as const }]
      : []),
    {
      id: "created_at",
      header: "Created",
      accessorKey: "created_at",
      kind: "datetime",
      enableSorting: true,
    },
    { id: "error", header: "Error", accessorKey: "error" },
  ];
  return (
    <DataTable
      label={partnerRunLabels[kind]}
      data={result.data?.items ?? []}
      columns={columns.map((column) => ({ enableSorting: false, ...column }))}
      getRowId={(row) => row.id}
      columnChoices
      loading={result.loading}
      error={result.error}
      onRetry={() => setRevision((n) => n + 1)}
      sort={sort}
      onSortChange={(next) => change({ sort: next })}
      pagination={{ offset, limit, total: result.data?.total ?? 0, onChange: change }}
      empty={`No ${partnerRunLabels[kind].toLowerCase()} found.`}
      toolbar={
        !embedded && (
          <>
            <SearchField
              label="Search runs"
              value={q}
              scopeKey={searchScope(query)}
              onSearch={(q) => change({ q })}
            />
            <Select
              label="Status"
              value={status || "all"}
              onChange={(value) => change({ status: value === "all" ? "" : value })}
              options={[
                { value: "all", label: "All statuses" },
                ...["queued", "running", "succeeded", "failed"].map((value) => ({
                  value,
                  label: value[0].toUpperCase() + value.slice(1),
                })),
              ]}
            />
            {kind === "pipeline" && (
              <Select
                label="Operation"
                value={operation || "all"}
                onChange={(value) => change({ operation: value === "all" ? "" : value })}
                options={[
                  { value: "all", label: "All operations" },
                  ...["sync", "sync_product", "assess"].map((value) => ({
                    value,
                    label: partnerOperation(value),
                  })),
                ]}
              />
            )}
            <Button variant="outline" onClick={() => setRevision((n) => n + 1)}>
              Refresh
            </Button>
            {(provider || productId || parentId) && (
              <Button
                variant="ghost"
                onClick={() => change({ provider: "", product_id: "", parent_id: "" })}
              >
                Clear related filters
              </Button>
            )}
          </>
        )
      }
    />
  );
}
