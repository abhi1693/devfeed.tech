"use client";

import { partnerSyncInterval } from "@/lib/partner-interval";

import { useRefreshInterval } from "@/lib/use-refresh-interval";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
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
    cell: ({ row }) => (
      <Link
        prefetch={false}
        href={importConnectionHref(row.original.provider)}
        className="font-medium text-primary hover:underline"
      >
        {row.original.name}
      </Link>
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
  const refreshSeconds = useRefreshInterval();
  const [items, setItems] = useState<ConnectionOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const connections = await adminPartnerConnectionsList();
        if (live) {
          setItems(connections);
          setError(undefined);
        }
      } catch (error) {
        if (live)
          setError(error instanceof Error ? error : new Error("Could not load import connections"));
      } finally {
        if (live) setLoading(false);
      }
    }
    void load();
    const timer =
      refreshSeconds > 0 ? setInterval(() => void load(), refreshSeconds * 1000) : undefined;
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [refresh, refreshSeconds]);
  return (
    <section className="space-y-6">
      <PageHeading title="Import connections" trail={partnershipTrail}>
        <Button asChild disabled={loading || !!error}>
          <Link href={`${importConnectionsPath}/new`} prefetch={false}>
            <Plus aria-hidden size={16} />
            Create import connection
          </Link>
        </Button>
      </PageHeading>
      <p className="text-sm text-muted-foreground">
        Connect product APIs and manage imports into partner accounts.
      </p>
      <DataTable
        label="Import connections"
        data={items}
        columns={columns}
        getRowId={(row) => row.provider}
        columnChoices
        loading={loading}
        error={error}
        onRetry={() => setRefresh((n) => n + 1)}
        empty="No import connections. Create an import connection to configure a product API."
      />
    </section>
  );
}
