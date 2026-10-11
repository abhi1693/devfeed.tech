"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { adminPartnerCampaignsList } from "@/lib/api/generated/admin";
import type { CampaignOut } from "@/lib/api/generated/models";
import { useRequest } from "@/lib/use-request";
import { accountHref } from "./partner-accounts";

export function PartnerCampaigns({ accountId }: { accountId: string }) {
  const [page, setPage] = useState({ offset: 0, limit: 25 });
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => adminPartnerCampaignsList(accountId, page, { signal }),
    [accountId, page],
  );
  const result = useRequest(
    `partner-campaigns/${accountId}/${page.offset}/${page.limit}/${revision}`,
    load,
  );
  const columns: DataTableColumn<CampaignOut>[] = [
    {
      accessorKey: "name",
      header: "Campaign",
      cell: ({ row }) => (
        <Link
          href={`${accountHref(accountId)}/campaigns/${row.original.id}/edit`}
          prefetch={false}
          className="text-primary hover:underline"
        >
          {row.original.name}
        </Link>
      ),
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge value={row.original.status} />,
    },
    {
      accessorKey: "targeting",
      header: "Products",
      cell: ({ row }) =>
        row.original.targeting === "all"
          ? "All eligible products"
          : row.original.targeting === "selected"
            ? `${row.original.product_ids?.length ?? 0} selected`
            : "Matching filters",
    },
    {
      accessorKey: "ends_at",
      header: "Ends",
      cell: ({ row }) => <DateTime value={row.original.ends_at} />,
    },
    { accessorKey: "impressions", header: "Impressions" },
    { accessorKey: "clicks", header: "Clicks" },
    {
      accessorKey: "ctr",
      header: "Click-through rate",
      cell: ({ row }) =>
        row.original.ctr == null ? "—" : `${(row.original.ctr * 100).toFixed(2)}%`,
    },
  ];
  return (
    <section className="space-y-4" aria-label="Paid campaigns">
      <div className="space-y-1">
        <h2 className="font-semibold">Paid campaigns</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Create a campaign for an agreed paid promotion. Account tiers do not activate sponsorship.
          Products must qualify and match the reader’s context before appearing. When a campaign
          ends, ordinary recommendation defaults continue to apply.
        </p>
      </div>
      <DataTable
        label="Paid campaigns"
        data={result.data?.items ?? []}
        columns={columns}
        getRowId={(row) => row.id}
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((value) => value + 1)}
        empty="No paid campaigns yet. Eligible products can still appear as ordinary recommendations."
        pagination={{
          ...page,
          total: result.data?.total ?? 0,
          onChange: (values) =>
            setPage((current) => ({
              offset: Number(values.offset ?? current.offset),
              limit: Number(values.limit ?? current.limit),
            })),
        }}
        toolbar={
          <Button asChild size="sm">
            <Link href={`${accountHref(accountId)}/campaigns/new`} prefetch={false}>
              <Plus aria-hidden />
              Create paid campaign
            </Link>
          </Button>
        }
      />
    </section>
  );
}
