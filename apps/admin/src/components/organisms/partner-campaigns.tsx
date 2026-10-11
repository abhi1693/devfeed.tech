"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { adminPartnerCampaignsList } from "@/lib/api/generated/admin";
import type { AccountOut, CampaignOut } from "@/lib/api/generated/models";
import { useRequest } from "@/lib/use-request";
import { accountHref } from "./partner-accounts";
import { hasAudience } from "@devfeed/ui/audience-targeting";

export function PartnerCampaigns({ account }: { account: AccountOut }) {
  const accountId = account.id;
  const canCreate =
    account.allowed_promotion_kinds?.includes("ad") ??
    ["gold", "platinum", "diamond"].includes(account.tier);
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
          href={
            row.original.managed
              ? `${accountHref(accountId)}/edit`
              : `${accountHref(accountId)}/campaigns/${row.original.id}/edit`
          }
          prefetch={false}
          className="text-primary hover:underline"
        >
          {row.original.name}
          {row.original.managed && (
            <span className="ml-2 text-xs text-muted-foreground">Account default</span>
          )}
          {row.original.grandfathered && (
            <span className="ml-2 text-xs text-muted-foreground">Existing agreement</span>
          )}
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
      accessorKey: "audience",
      header: "Audience",
      cell: ({ row }) => {
        const audience = row.original.audience;
        if (audience == null) return "Account defaults";
        if (!hasAudience(audience)) return "Any audience";
        return [
          audience.experience_levels?.length &&
            `${audience.experience_levels.length} experience levels`,
          audience.language_topic_ids?.length && `${audience.language_topic_ids.length} languages`,
          audience.tool_topic_ids?.length && `${audience.tool_topic_ids.length} tools`,
        ]
          .filter(Boolean)
          .join(" · ");
      },
    },
    {
      accessorKey: "ends_at",
      header: "Ends",
      cell: ({ row }) =>
        row.original.managed ? (
          "Follows account defaults"
        ) : (
          <DateTime value={row.original.ends_at} />
        ),
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
          Sponsored account defaults create a standing campaign covering accepted imported products.
          Create additional campaigns for specific dates or product selections on Gold, Platinum, or
          Diamond. Contextual matches are preferred; sponsored defaults can fill remaining slots in
          the main feed. Article, topic, and search placements require a relevant match.
        </p>
        {!canCreate && (
          <p className="text-sm text-muted-foreground">
            This tier supports product recommendations. Existing active dated agreements continue
            until their recorded end.
          </p>
        )}
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
          canCreate ? (
            <Button asChild size="sm">
              <Link href={`${accountHref(accountId)}/campaigns/new`} prefetch={false}>
                <Plus aria-hidden />
                Create paid campaign
              </Link>
            </Button>
          ) : undefined
        }
      />
    </section>
  );
}
