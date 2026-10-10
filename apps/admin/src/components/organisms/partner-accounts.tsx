"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import type { AccountOut, AccountsOut } from "@/lib/api/generated/models";

export const accountsPath = "/partnerships/accounts";
export const accountHref = (id: string) => `${accountsPath}/${encodeURIComponent(id)}`;
export const accountsTrail = [{ label: "Partnerships", href: "/partnerships" }];
const columns: DataTableColumn<AccountOut>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <Link
        href={accountHref(row.original.id)}
        prefetch={false}
        className="font-medium text-primary hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    accessorKey: "tier",
    header: "Partnership tier",
    accessorFn: (row) => row.tier.charAt(0).toUpperCase() + row.tier.slice(1),
  },
  { accessorKey: "status", header: "Status", kind: "pill" },
  {
    id: "actions",
    header: "Actions",
    cell: ({ row }) => (
      <Button asChild variant="outline" size="sm">
        <Link
          href={`${accountHref(row.original.id)}/edit`}
          prefetch={false}
          aria-label={`Edit ${row.original.name}`}
        >
          Edit
        </Link>
      </Button>
    ),
  },
];

export function PartnerAccounts() {
  const [accounts, setAccounts] = useState<AccountsOut>({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/v1/admin/partner-accounts?offset=${offset}&limit=${limit}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load partner accounts.");
        return response.json() as Promise<AccountsOut>;
      })
      .then((page) => {
        if (!controller.signal.aborted) {
          setAccounts(page);
          setError(undefined);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [offset, limit, revision]);
  return (
    <section className="space-y-6">
      <PageHeading
        title="Partner accounts"
        description="Manage commercial partnerships and partner access."
        trail={accountsTrail}
      >
        <Button asChild size="sm">
          <Link href={`${accountsPath}/new`} prefetch={false}>
            <Plus aria-hidden />
            Create partner account
          </Link>
        </Button>
      </PageHeading>
      <DataTable
        label="Partner accounts"
        data={accounts.items}
        columns={columns}
        getRowId={(row) => row.id}
        loading={loading}
        error={error}
        onRetry={() => setRevision((n) => n + 1)}
        empty="No partner accounts."
        pagination={{
          offset,
          limit,
          total: accounts.total,
          onChange: (values) => {
            setLoading(true);
            setOffset(Number(values.offset));
            if (values.limit) setLimit(Number(values.limit));
          },
        }}
      />
    </section>
  );
}
