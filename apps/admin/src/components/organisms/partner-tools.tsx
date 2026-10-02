"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { PageHeading } from "@/components/molecules/page-heading";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { adminPartnerToolsList } from "@/lib/api/generated/admin";
import type { ProductOut } from "@/lib/api/generated/models";
import { partnerProductHref } from "@/lib/partner-runs";
import { useRequest } from "@/lib/use-request";
import { useTableQuery } from "@/lib/use-table-query";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { partnershipTrail } from "./partner-connections";

export const productColumns: DataTableColumn<ProductOut>[] = [
  {
    id: "name",
    header: "Name",
    accessorKey: "name",
    enableSorting: true,
    cell: ({ row }) => (
      <Link
        prefetch={false}
        href={partnerProductHref(row.original.id)}
        className="font-medium text-primary hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    id: "assessment",
    header: "Assessment",
    accessorFn: (row) => (row.excluded ? "Excluded" : row.assessment.state),
    kind: "pill",
  },
  {
    id: "platforms",
    header: "Platforms",
    accessorFn: (row) =>
      [...new Set(row.listings.map((listing) => listing.platform_name))].join(", "),
  },
  { id: "pricing", header: "Pricing", accessorKey: "pricing" },
  {
    id: "updated_at",
    header: "Updated",
    accessorKey: "updated_at",
    kind: "datetime",
    enableSorting: true,
  },
];

export function PartnerTools({ provider }: { provider?: string }) {
  const query = useTableQuery("partner-products").toString();
  return <ProductTable query={query} provider={provider} />;
}

function ProductTable({ query, provider }: { query: string; provider?: string }) {
  const params = new URLSearchParams(query);
  const router = useRouter();
  const q = params.get("q") ?? "";
  const sort = params.get("sort") ?? "-updated_at";
  const bounded = (value: string | null, fallback: number, max: number) =>
    value !== null && /^\d+$/.test(value) ? Math.min(max, Number(value)) : fallback;
  const offset = bounded(params.get("offset"), 0, 100000000);
  const limit = Math.max(1, bounded(params.get("limit"), 25, 100));
  const [revision, setRevision] = useState(0);
  const interval = useRefreshInterval();
  const load = useCallback(
    async (signal: AbortSignal) =>
      adminPartnerToolsList({ provider, q, sort, offset, limit }, { signal }),
    [provider, q, sort, offset, limit],
  );
  const result = useRequest(
    `partner-products/${provider}/${query}/${revision}`,
    load,
    interval * 1000,
  );
  function change(values: Record<string, string>) {
    const next = new URLSearchParams(query);
    next.set("offset", "0");
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    router.replace(`/partnerships/products?${next}`, { scroll: false });
  }
  return (
    <section className="space-y-6">
      <PageHeading title="Products" trail={partnershipTrail} />
      <DataTable
        label="Products"
        data={result.data?.items ?? []}
        columns={productColumns.map((column) => ({ enableSorting: false, ...column }))}
        getRowId={(row) => row.id}
        columnChoices
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((n) => n + 1)}
        sort={sort}
        onSortChange={(sort) => change({ sort })}
        pagination={{ offset, limit, total: result.data?.total ?? 0, onChange: change }}
        empty={
          q
            ? "No products match your search."
            : "No synced products. Manage connections in Partners."
        }
        toolbar={
          <>
            <SearchField
              label="Search products"
              value={q}
              scopeKey={searchScope(query)}
              onSearch={(q) => change({ q })}
            />
            <Button variant="outline" onClick={() => setRevision((n) => n + 1)}>
              Refresh
            </Button>
            {provider && (
              <Button variant="ghost" onClick={() => change({ provider: "" })}>
                Clear partner filter
              </Button>
            )}
          </>
        }
      />
    </section>
  );
}
