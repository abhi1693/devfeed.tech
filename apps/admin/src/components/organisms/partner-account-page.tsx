"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Pencil, Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { InfoPanel, DataValue } from "@/components/molecules/info-panel";
import { StatusBadge } from "@/components/molecules/status-badge";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { useAdmin } from "@/components/molecules/admin-session";
import type { Dashboard, AssetMetrics, MemberOut } from "@/lib/api/generated/models";
import { PartnerAccountManagement } from "./partner-account-management";
import { accountsPath, accountHref, accountsTrail } from "./partner-accounts";

type Member = MemberOut;
export type AccountSection =
  "details" | "edit" | "related" | "members/new" | "assets/new" | `assets/${string}/edit`;
export function PartnerAccountPage({
  id,
  section = "details",
  initialOffset = 0,
  initialLimit = 100,
}: {
  id?: string;
  section?: AccountSection;
  initialOffset?: number;
  initialLimit?: number;
}) {
  const admin = useAdmin();
  const [data, setData] = useState<Dashboard>();
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(initialOffset);
  const [limit, setLimit] = useState(initialLimit);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    Promise.all([
      fetch(`/api/v1/admin/partner-accounts/${id}/dashboard?offset=${offset}&limit=${limit}`, {
        cache: "no-store",
        signal: controller.signal,
      }),
      fetch(`/api/v1/admin/partner-accounts/${id}/members`, {
        cache: "no-store",
        signal: controller.signal,
      }),
    ])
      .then(async (responses) => {
        if (responses.some((response) => !response.ok))
          throw new Error("Could not load partner account details.");
        const [dashboard, membership] = await Promise.all(
          responses.map((response) => response.json()),
        );
        if (!controller.signal.aborted) {
          setData(dashboard);
          setMembers(membership);
          setError(undefined);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason);
      });
    return () => controller.abort();
  }, [id, offset, limit, revision]);
  const href = id ? accountHref(id) : accountsPath;
  const editingAsset = section.startsWith("assets/") && section.endsWith("/edit");
  const asset = editingAsset
    ? data?.assets.find((item) => item.id === section.split("/")[1])
    : undefined;
  const form =
    !id ||
    section === "edit" ||
    section === "members/new" ||
    section === "assets/new" ||
    editingAsset;
  const title = !id
    ? "Create partner account"
    : section === "edit"
      ? `Edit ${data?.account.name ?? "partner account"}`
      : section === "members/new"
        ? "Add member"
        : section === "assets/new"
          ? "Associate asset"
          : editingAsset
            ? `Edit ${asset?.name ?? "asset"}`
            : (data?.account.name ?? "Partner account");
  async function removeMember(subject: string) {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/v1/admin/partner-accounts/${id}/members/${encodeURIComponent(subject)}`,
        { method: "DELETE", headers: { "x-csrf-token": admin.csrf_token } },
      );
      if (!response.ok) throw new Error("Could not remove membership.");
      setRevision((n) => n + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error("Could not remove membership."));
    } finally {
      setBusy(false);
    }
  }
  const memberColumns: DataTableColumn<Member>[] = [
    {
      accessorKey: "name",
      header: "Name",
      cell: ({ row }) =>
        row.original.user_id ? (
          <Link
            href={`/users/${row.original.user_id}`}
            className="hover:underline"
            prefetch={false}
          >
            {row.original.name || "Unnamed user"}
          </Link>
        ) : (
          "User details unavailable"
        ),
    },
    { accessorKey: "email", header: "Email", cell: ({ row }) => row.original.email || "—" },
    { accessorKey: "subject", header: "Zitadel subject ID" },
    { accessorKey: "issuer", header: "Issuer" },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Remove member ${row.original.subject}`}
          onClick={() => void removeMember(row.original.subject)}
        >
          Remove
        </Button>
      ),
    },
  ];
  const assetColumns: DataTableColumn<AssetMetrics>[] = [
    { accessorKey: "name", header: "Name" },
    { accessorKey: "kind", header: "Type" },
    { accessorKey: "status", header: "Status", kind: "pill" },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <Button asChild variant="outline" size="sm">
          <Link
            href={`${href}/assets/${row.original.id}/edit?offset=${offset}&limit=${limit}`}
            prefetch={false}
            aria-label={`Edit ${row.original.name}`}
          >
            Edit
          </Link>
        </Button>
      ),
    },
  ];
  return (
    <section className={form ? "max-w-4xl space-y-6" : "space-y-6"}>
      <PageHeading
        title={title}
        trail={[
          ...accountsTrail,
          { label: "Partner accounts", href: accountsPath },
          ...(form && data ? [{ label: data.account.name, href }] : []),
        ]}
      >
        {id && data && !form && (
          <Button asChild variant="outline" size="sm">
            <Link href={`${href}/edit`} prefetch={false}>
              <Pencil aria-hidden />
              Edit
            </Link>
          </Button>
        )}
      </PageHeading>
      <RequestState
        loading={!!id && !data && !error}
        error={error}
        retry={() => setRevision((n) => n + 1)}
      />
      {!error &&
        (!id || data) &&
        (form ? (
          editingAsset && !asset ? (
            <p role="alert">
              This asset is unavailable. Return to related objects to select an asset.
            </p>
          ) : (
            <PartnerAccountManagement
              key={`${id ?? "new"}/${section}`}
              account={data?.account}
              asset={asset}
              mode={
                section === "members/new"
                  ? "member"
                  : section.startsWith("assets/")
                    ? "asset"
                    : "account"
              }
            />
          )
        ) : (
          <>
            <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
              {(["details", "related"] as const).map((tab) => (
                <Link
                  key={tab}
                  prefetch={false}
                  href={href + (tab === "details" ? "" : "/related")}
                  aria-current={section === tab ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${section === tab ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                >
                  {tab === "details" ? "Details" : "Related objects"}
                </Link>
              ))}
            </nav>
            {section === "related" ? (
              <div className="space-y-6">
                <DataTable
                  label="Account members"
                  data={members}
                  columns={memberColumns}
                  getRowId={(row) => `${row.issuer}/${row.subject}`}
                  empty="No members added yet."
                  toolbar={
                    <Button asChild size="sm">
                      <Link href={`${href}/members/new`} prefetch={false}>
                        <Plus aria-hidden />
                        Add member
                      </Link>
                    </Button>
                  }
                />
                <DataTable
                  label="Products & ads"
                  data={data!.assets}
                  columns={assetColumns}
                  getRowId={(row) => row.id}
                  empty="No assets associated yet."
                  pagination={{
                    offset,
                    limit,
                    total: data!.asset_total,
                    onChange: (values) => {
                      setOffset(Number(values.offset));
                      if (values.limit) setLimit(Number(values.limit));
                    },
                  }}
                  toolbar={
                    <Button asChild size="sm">
                      <Link href={`${href}/assets/new`} prefetch={false}>
                        <Plus aria-hidden />
                        Associate asset
                      </Link>
                    </Button>
                  }
                />
              </div>
            ) : (
              <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
                <InfoPanel
                  title="Partnership"
                  fields={[
                    { label: "Name", value: <DataValue value={data!.account.name} /> },
                    {
                      label: "Partnership tier",
                      value: (
                        <DataValue
                          value={
                            data!.account.tier.charAt(0).toUpperCase() + data!.account.tier.slice(1)
                          }
                        />
                      ),
                    },
                    { label: "Status", value: <StatusBadge value={data!.account.status} /> },
                    { label: "Benefits", value: <DataValue value={data!.account.benefits} /> },
                  ]}
                />
                <InfoPanel
                  title="Record information"
                  fields={[
                    { label: "ID", value: <DataValue value={id} /> },
                    {
                      label: "Members",
                      value: (
                        <Link href={`${href}/related`} className="text-primary hover:underline">
                          {members.length}
                        </Link>
                      ),
                    },
                    {
                      label: "Products & ads",
                      value: (
                        <Link href={`${href}/related`} className="text-primary hover:underline">
                          {data!.asset_total}
                        </Link>
                      ),
                    },
                  ]}
                />
              </div>
            )}
          </>
        ))}
    </section>
  );
}
