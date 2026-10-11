"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { RequestState } from "@/components/molecules/request-state";
import { SearchField } from "@/components/molecules/search-field";
import { StatusBadge } from "@/components/molecules/status-badge";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  adminPartnerInventoryList,
  adminPartnerProductPromotionUpdate,
} from "@/lib/api/generated/admin";
import type { AccountOut, InventoryProductOut } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { useRequest } from "@/lib/use-request";
import { partnerProductHref } from "@/lib/partner-runs";

export function PartnerInventory({ account }: { account: AccountOut }) {
  const admin = useAdmin();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState({ offset: 0, limit: 25 });
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<Error>();
  const load = useCallback(
    (signal: AbortSignal) =>
      adminPartnerInventoryList(account.id, { ...page, q: query || undefined }, { signal }),
    [account.id, page, query],
  );
  const result = useRequest(
    `partner-inventory/${account.id}/${query}/${page.offset}/${page.limit}/${revision}`,
    load,
  );
  async function toggle(product: InventoryProductOut) {
    setBusy(product.id);
    setError(undefined);
    try {
      await adminPartnerProductPromotionUpdate(
        account.id,
        product.id,
        { excluded: !product.excluded },
        { headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      setRevision((value) => value + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) returnToLogin();
      setError(reason instanceof Error ? reason : new Error("Could not update product."));
    } finally {
      setBusy(undefined);
    }
  }
  const columns: DataTableColumn<InventoryProductOut>[] = [
    {
      accessorKey: "name",
      header: "Product",
      meta: { headerClassName: "w-[24%]", className: "whitespace-normal" },
      cell: ({ row }) => (
        <div className="space-y-2 py-1">
          <Link
            href={partnerProductHref(row.original.id)}
            prefetch={false}
            className="font-medium break-words text-primary hover:underline"
          >
            {row.original.name}
          </Link>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>Developer check</span>
            <StatusBadge value={row.original.status} />
          </div>
        </div>
      ),
    },
    {
      accessorKey: "commercial_approved",
      header: "Acceptance",
      meta: { headerClassName: "w-[18%]", className: "whitespace-normal" },
      cell: ({ row }) => (
        <div className="space-y-1.5">
          <StatusBadge
            value={row.original.commercial_approved ? "Accepted" : "Pending"}
            tone={row.original.commercial_approved ? "success" : "warning"}
          />
          {row.original.commercial_approved && (
            <p className="text-xs text-muted-foreground">
              {row.original.approval_source === "trusted" ? "Trusted partner" : "Verified product"}
            </p>
          )}
        </div>
      ),
    },
    {
      accessorKey: "technologies",
      header: "Technologies",
      meta: {
        headerClassName: "w-[28%]",
        className: "whitespace-normal break-words leading-5 text-muted-foreground",
      },
      cell: ({ row }) => row.original.technologies.join(", ") || "—",
    },
    {
      accessorKey: "excluded",
      header: "Promotion",
      meta: { headerClassName: "w-[16%]", className: "whitespace-normal" },
      cell: ({ row }) => (
        <div className="text-sm leading-5 text-muted-foreground">
          {row.original.globally_excluded ? (
            "Blocked by product checks"
          ) : row.original.excluded ? (
            <StatusBadge value="Excluded" />
          ) : (
            "Inherits account defaults"
          )}
        </div>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      meta: { headerClassName: "w-[14%]", className: "whitespace-normal" },
      cell: ({ row }) => (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={
            !!busy || result.loading || (row.original.globally_excluded && row.original.excluded)
          }
          loading={busy === row.original.id}
          aria-label={`${row.original.excluded ? "Allow" : "Exclude"} ${row.original.name}`}
          onClick={() => void toggle(row.original)}
        >
          {row.original.excluded ? "Allow" : "Exclude"}
        </Button>
      ),
    },
  ];
  return (
    <section className="min-w-0 space-y-5" aria-label="Account products">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <h2 className="font-semibold">Imported products</h2>
          <p className="text-sm text-muted-foreground">
            Products inherit this account’s promotion defaults.
          </p>
        </div>
        <SearchField
          value={query}
          label="Product name"
          placeholder="Search imported products…"
          scopeKey={account.id}
          className="w-full sm:w-72 sm:shrink-0"
          onSearch={(value) => {
            setQuery(value);
            setPage((current) => ({ ...current, offset: 0 }));
          }}
        />
      </div>
      {!account.auto_include_products && (
        <p className="text-sm text-muted-foreground">
          Automatic inclusion is off. Products can only appear through campaigns that select them
          explicitly until you enable automatic inclusion in account settings.
        </p>
      )}
      <RequestState error={error} />
      <DataTable
        label="Account products"
        className="min-w-[54rem] table-fixed"
        data={result.data?.items ?? []}
        columns={columns}
        getRowId={(row) => row.id}
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((value) => value + 1)}
        empty="No matching imported products. Connect an import source to this account to populate its catalog."
        pagination={{
          ...page,
          total: result.data?.total ?? 0,
          onChange: (values) =>
            setPage((current) => ({
              offset: Number(values.offset ?? current.offset),
              limit: Number(values.limit ?? current.limit),
            })),
        }}
      />
    </section>
  );
}
