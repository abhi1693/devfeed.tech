"use client";

import { partnerSyncInterval } from "@/lib/partner-interval";

import { useRefreshInterval } from "@/lib/use-refresh-interval";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { ConnectionLogo } from "@devfeed/ui/connection-logo";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { useRequest } from "@/lib/use-request";
import { useTableQuery } from "@/lib/use-table-query";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { adminPartnerConnectionsList } from "@/lib/api/generated/admin";
import type { ConnectionOut } from "@/lib/api/generated/models";

export const importConnectionsPath = "/partnerships/connections";
export const importConnectionHref = (provider: string) =>
  `${importConnectionsPath}/${encodeURIComponent(provider)}`;
export const partnershipTrail = [{ label: "Partnerships", href: "/partnerships" }];
const columns: DataTableColumn<ConnectionOut>[] = [
  {
    id: "name",
    accessorKey: "name",
    header: "Name",
    enableSorting: true,
    cell: ({ row }) => (
      <span className="inline-flex items-center gap-3">
        <ConnectionLogo
          name={row.original.name}
          url={row.original.logo_url}
          variants={row.original.logo_variants}
        />
        <Link
          prefetch={false}
          href={importConnectionHref(row.original.provider)}
          className="font-medium text-primary hover:underline"
        >
          {row.original.name}
        </Link>
      </span>
    ),
  },
  { id: "type", header: "Type", cell: () => "Launch platform" },
  {
    id: "sync_interval",
    header: "Sync interval",
    accessorFn: (row) => partnerSyncInterval(row.sync_interval_minutes),
  },

  {
    id: "enabled",
    header: "Enabled",
    accessorFn: (row) => (row.enabled ? "Enabled" : "Disabled"),
    kind: "pill",
  },
  {
    id: "state",
    header: "Sync status",
    accessorFn: (row) =>
      !row.enabled
        ? "Paused"
        : row.state === "syncing"
          ? "Syncing"
          : row.state === "error"
            ? "Failed"
            : "Idle",
    kind: "pill",
  },
  { id: "products", header: "Products", accessorKey: "products", kind: "number" },
  {
    id: "commercial_approved",
    header: "Accepted for account",
    accessorFn: (row) => row.commercial_approved ?? 0,
    kind: "number",
  },
  { id: "last_sync_at", header: "Last synced", accessorKey: "last_sync_at", kind: "datetime" },
  {
    id: "actions",
    header: "Actions",
    enableHiding: false,
    cell: ({ row }) => (
      <Button asChild variant="outline" size="sm">
        <Link
          prefetch={false}
          aria-label={`Edit ${row.original.name}`}
          href={`${importConnectionHref(row.original.provider)}/edit`}
        >
          Edit
        </Link>
      </Button>
    ),
  },
];

export function PartnerConnections() {
  const query = useTableQuery("partner-connections").toString();
  return <ConnectionTable query={query} />;
}

function ConnectionTable({ query }: { query: string }) {
  const router = useRouter();
  const params = new URLSearchParams(query);
  const bounded = (value: string | null, fallback: number, max: number) =>
    value !== null && /^\d+$/.test(value) ? Math.min(max, Number(value)) : fallback;
  const offset = bounded(params.get("offset"), 0, 100000000);
  const limit = Math.max(1, bounded(params.get("limit"), 25, 100));
  const requestedSort = params.get("sort") ?? "provider";
  const sort = ["provider", "-provider", "name", "-name"].includes(requestedSort)
    ? requestedSort
    : "provider";
  const q = params.get("q") ?? "";
  const refreshSeconds = useRefreshInterval();
  const [refresh, setRefresh] = useState(0);
  const load = useCallback(
    async (signal: AbortSignal) =>
      adminPartnerConnectionsList({ q, sort, offset, limit }, { signal }),
    [q, sort, offset, limit],
  );
  const result = useRequest(`partner-connections/${query}/${refresh}`, load, refreshSeconds * 1000);
  function change(values: Record<string, string>) {
    const next = new URLSearchParams(query);
    next.set("offset", "0");
    Object.entries(values).forEach(([key, value]) =>
      value ? next.set(key, value) : next.delete(key),
    );
    router.replace(`${importConnectionsPath}?${next}`, { scroll: false });
  }
  return (
    <section className="space-y-6">
      <ResourceHeading title="Import connections" trail={partnershipTrail}>
        <Button asChild disabled={result.loading || !!result.error}>
          <Link href={`${importConnectionsPath}/new`} prefetch={false}>
            <Plus aria-hidden size={16} />
            Create import connection
          </Link>
        </Button>
      </ResourceHeading>
      <p className="text-sm text-muted-foreground">
        Connect product APIs and manage imports into partner accounts.
      </p>
      <DataTable
        label="Import connections"
        data={result.data?.items ?? []}
        columns={columns.map((column) => ({ enableSorting: false, ...column }))}
        getRowId={(row) => row.provider}
        columnChoices
        loading={result.loading}
        error={result.error}
        onRetry={() => setRefresh((n) => n + 1)}
        sort={sort}
        onSortChange={(next) => change({ sort: next })}
        pagination={{ offset, limit, total: result.data?.total ?? 0, onChange: change }}
        toolbar={
          <SearchField
            label="Search import connections"
            value={q}
            scopeKey={searchScope(query)}
            onSearch={(value) => change({ q: value })}
          />
        }
        empty={
          q
            ? "No import connections match your search."
            : "No import connections. Create an import connection to configure a product API."
        }
      />
    </section>
  );
}
