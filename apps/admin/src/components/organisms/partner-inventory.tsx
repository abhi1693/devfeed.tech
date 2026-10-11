"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { RequestState } from "@/components/molecules/request-state";
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
  const [search, setSearch] = useState("");
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
      cell: ({ row }) => (
        <Link
          href={partnerProductHref(row.original.id)}
          prefetch={false}
          className="text-primary hover:underline"
        >
          {row.original.name}
        </Link>
      ),
    },
    { accessorKey: "status", header: "Qualification", kind: "pill" },
    {
      accessorKey: "technologies",
      header: "Technologies",
      cell: ({ row }) => row.original.technologies.join(", ") || "—",
    },
    {
      accessorKey: "excluded",
      header: "Promotion policy",
      cell: ({ row }) => (
        <StatusBadge
          value={
            row.original.globally_excluded
              ? "Blocked by product checks"
              : row.original.excluded
                ? "Excluded"
                : "Inherits account defaults"
          }
        />
      ),
    },
    {
      id: "actions",
      header: "Actions",
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
          {row.original.excluded ? "Allow product" : "Exclude product"}
        </Button>
      ),
    },
  ];
  return (
    <section className="space-y-4" aria-label="Account products">
      <div className="space-y-1">
        <h2 className="font-semibold">Imported products</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Products belong to this account through its import connections. Allowed products inherit
          account defaults and still require qualification and a relevant match. Excluding a product
          blocks both ordinary recommendations and paid campaigns for this account.
        </p>
        {!account.auto_include_products && (
          <p className="text-sm text-muted-foreground">
            Automatic inclusion is off. Products can only appear through campaigns that select them
            explicitly until you enable automatic inclusion in account settings.
          </p>
        )}
      </div>
      <RequestState error={error} />
      <DataTable
        label="Account products"
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
        toolbar={
          <form
            className="flex flex-wrap gap-2"
            role="search"
            aria-label="Search account products"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery(search.trim());
              setPage((current) => ({ ...current, offset: 0 }));
            }}
          >
            <Input
              type="search"
              aria-label="Product name"
              placeholder="Search imported products…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="max-w-72"
            />
            <Button type="submit" size="sm" variant="outline">
              Search
            </Button>
          </form>
        }
      />
    </section>
  );
}
