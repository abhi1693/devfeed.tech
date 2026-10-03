"use client";

import { partnerSyncInterval } from "@/lib/partner-interval";

import { useRefreshInterval } from "@/lib/use-refresh-interval";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { adminPartnerConnectionsList, adminPartnerProvidersList } from "@/lib/api/generated/admin";
import type { ConnectionOut } from "@/lib/api/generated/models";

export const partnersPath = "/partnerships/partners";
export const partnerHref = (provider: string) => `${partnersPath}/${encodeURIComponent(provider)}`;
export const partnershipTrail = [{ label: "Partnerships", href: "/partnerships" }];
const columns: DataTableColumn<ConnectionOut>[] = [
  {
    id: "name",
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <Link
        prefetch={false}
        href={partnerHref(row.original.provider)}
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
          href={`${partnerHref(row.original.provider)}/edit`}
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
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [connections, providers] = await Promise.all([
          adminPartnerConnectionsList(),
          adminPartnerProvidersList(),
        ]);
        if (live) {
          setItems(connections);
          setAvailable(providers.some((p) => !connections.some((c) => c.provider === p.provider)));
          setError(undefined);
        }
      } catch (error) {
        if (live) setError(error instanceof Error ? error : new Error("Could not load partners"));
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
      <PageHeading title="Partners" trail={partnershipTrail}>
        <Button
          asChild
          disabled={loading || !!error || !available}
          title={!loading && !available ? "All supported partners have been added" : undefined}
        >
          <Link href={`${partnersPath}/new`} prefetch={false}>
            <Plus aria-hidden size={16} />
            Create partner
          </Link>
        </Button>
      </PageHeading>
      <DataTable
        label="Partners"
        data={items}
        columns={columns}
        getRowId={(row) => row.provider}
        columnChoices
        loading={loading}
        error={error}
        onRetry={() => setRefresh((n) => n + 1)}
        empty="No partners. Create a partner to connect a supported platform."
      />
    </section>
  );
}
