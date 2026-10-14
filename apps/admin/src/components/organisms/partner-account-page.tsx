"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { RequestState } from "@/components/molecules/request-state";
import { ResourcePanel, DataValue } from "@devfeed/ui/resource-layout";
import { audienceValue } from "@devfeed/ui/audience-targeting";
import { PartnerAudience } from "./partner-audience";
import { adFormatLabel } from "@devfeed/ui/advertising-formats";
import { StatusBadge } from "@/components/molecules/status-badge";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { useAdmin } from "@/components/molecules/admin-session";
import type { Dashboard, AssetMetrics, MemberOut } from "@/lib/api/generated/models";
import { PartnerAccountManagement } from "./partner-account-management";
import {
  accountsPath,
  accountHref,
  accountsTrail,
  partnerAccountHref,
  type PartnerAccountRoute,
} from "@/lib/partner-account-routes";
import { PartnerCampaigns } from "./partner-campaigns";
import { PartnerCampaignPage } from "./partner-campaign-form";
import { PartnerInventory } from "./partner-inventory";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { adminPartnerAssetGet } from "@/lib/api/generated/admin";
import { useRequest } from "@/lib/use-request";

type Member = MemberOut;
export function PartnerAccountPage({
  route,
  initialOffset = 0,
  initialLimit = 100,
}: {
  route: PartnerAccountRoute;
  initialOffset?: number;
  initialLimit?: number;
}) {
  const admin = useAdmin();
  const accountId =
    route.resource === "account" && route.action === "create" ? undefined : route.accountId;
  const [dashboard, setDashboard] = useState<{ accountId: string; data: Dashboard }>();
  const data =
    dashboard?.accountId && dashboard.accountId === accountId ? dashboard.data : undefined;
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(initialOffset);
  const [limit, setLimit] = useState(initialLimit);
  const assetId =
    route.resource === "asset" && route.action === "edit" ? route.identifier : undefined;
  const [assetRevision, setAssetRevision] = useState(0);
  const loadAsset = useCallback(
    async (signal: AbortSignal) => {
      if (!accountId || !assetId) return null;
      try {
        return await adminPartnerAssetGet(accountId, assetId, { signal });
      } catch (reason) {
        if (reason instanceof ApiError && [404, 422].includes(reason.status)) return null;
        throw reason;
      }
    },
    [accountId, assetId],
  );
  const assetResult = useRequest(
    `partner-asset/${accountId}/${assetId}/${assetRevision}`,
    loadAsset,
  );
  const asset = assetResult.data ?? undefined;
  useEffect(() => {
    if (!accountId) return;
    const controller = new AbortController();
    Promise.all([
      fetch(
        `/api/v1/admin/partner-accounts/${accountId}/dashboard?offset=${offset}&limit=${limit}`,
        {
          cache: "no-store",
          signal: controller.signal,
        },
      ),
      fetch(`/api/v1/admin/partner-accounts/${accountId}/members`, {
        cache: "no-store",
        signal: controller.signal,
      }),
    ])
      .then(async (responses) => {
        if (responses.some((response) => response.status === 401)) {
          returnToLogin();
          throw new ApiError(401);
        }
        if (responses.some((response) => !response.ok))
          throw new Error("Could not load partner account details.");
        const [dashboard, membership] = await Promise.all(
          responses.map((response) => response.json()),
        );
        if (!controller.signal.aborted) {
          setDashboard({ accountId, data: dashboard });
          setMembers(membership);
          setError(undefined);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason);
      });
    return () => controller.abort();
  }, [accountId, offset, limit, revision]);
  const href = accountId ? accountHref(accountId) : accountsPath;
  const editingAsset = route.resource === "asset" && route.action === "edit";
  const campaignForm = route.resource === "campaign" && route.action !== "list";
  const form = route.action === "create" || route.action === "edit";
  const title =
    route.resource === "account" && route.action === "create"
      ? "Create partner account"
      : route.resource === "account" && route.action === "edit"
        ? `Edit ${data?.account.name ?? "partner account"}`
        : route.resource === "member"
          ? "Add member"
          : campaignForm
            ? route.action === "create"
              ? "Create paid campaign"
              : "Edit paid campaign"
            : route.resource === "asset" && route.action === "create"
              ? "Associate asset"
              : editingAsset
                ? `Edit ${asset?.name ?? "asset"}`
                : (data?.account.name ?? "Partner account");
  async function removeMember(subject: string) {
    if (!accountId) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/v1/admin/partner-accounts/${accountId}/members/${encodeURIComponent(subject)}`,
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
    {
      accessorKey: "ad_formats",
      header: "Format",
      cell: ({ row }) =>
        (row.original.ad_formats?.length
          ? row.original.ad_formats
          : [row.original.ad_format ?? "feed"]
        )
          .map(adFormatLabel)
          .join(" · "),
    },
    {
      id: "association",
      header: "Association",
      cell: ({ row }) =>
        row.original.aggregated
          ? "Product totals"
          : row.original.automatic
            ? "Automatic"
            : "Legacy manual asset",
    },
    { accessorKey: "status", header: "Status", kind: "pill" },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <Button asChild variant="outline" size="sm">
          <Link
            href={
              row.original.aggregated
                ? partnerAccountHref({ resource: "account", action: "edit", accountId: accountId! })
                : row.original.automatic
                  ? row.original.campaign_id
                    ? partnerAccountHref({
                        resource: "campaign",
                        action: "edit",
                        accountId: accountId!,
                        identifier: row.original.campaign_id,
                      })
                    : partnerAccountHref({
                        resource: "account",
                        action: "edit",
                        accountId: accountId!,
                      })
                  : `${partnerAccountHref({
                      resource: "asset",
                      action: "edit",
                      accountId: accountId!,
                      identifier: row.original.id,
                    })}?offset=${offset}&limit=${limit}`
            }
            prefetch={false}
            aria-label={
              row.original.automatic || row.original.aggregated
                ? `View policy for ${row.original.name}`
                : `Edit ${row.original.name}`
            }
          >
            {row.original.automatic || row.original.aggregated ? "View policy" : "Edit"}
          </Link>
        </Button>
      ),
    },
  ];
  return (
    <section className={form ? "max-w-4xl space-y-6" : "space-y-6"}>
      <ResourceHeading
        title={title}
        trail={[
          ...accountsTrail,
          { label: "Partner accounts", href: accountsPath },
          ...(form && data ? [{ label: data.account.name, href }] : []),
        ]}
      >
        {accountId && data && !form && (
          <Button asChild variant="outline" size="sm">
            <Link
              href={partnerAccountHref({ resource: "account", action: "edit", accountId })}
              prefetch={false}
            >
              <Pencil aria-hidden />
              Edit
            </Link>
          </Button>
        )}
      </ResourceHeading>
      <RequestState
        loading={!!accountId && !data && !error}
        error={error}
        retry={() => setRevision((n) => n + 1)}
      />
      {!error &&
        (!accountId || data) &&
        (campaignForm && data ? (
          <PartnerCampaignPage
            account={data.account}
            campaignId={
              route.resource === "campaign" && route.action === "edit"
                ? route.identifier
                : undefined
            }
          />
        ) : form ? (
          editingAsset && (assetResult.loading || assetResult.error) ? (
            <RequestState
              loading={assetResult.loading}
              error={assetResult.error}
              retry={() => setAssetRevision((value) => value + 1)}
            />
          ) : editingAsset && !asset ? (
            <p role="alert">
              This asset is unavailable. Return to related objects to select an asset.
            </p>
          ) : editingAsset && asset?.automatic ? (
            <p role="alert">
              This reporting record is managed automatically. Change promotion settings in the
              account defaults or its paid campaign.
            </p>
          ) : (
            <PartnerAccountManagement
              key={partnerAccountHref(route)}
              account={data?.account}
              asset={asset}
              mode={
                route.resource === "member"
                  ? "member"
                  : route.resource === "asset"
                    ? "asset"
                    : "account"
              }
            />
          )
        ) : (
          <>
            <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
              {(
                [
                  { label: "Details", resource: "account", action: "view" },
                  { label: "Products", resource: "product", action: "list" },
                  { label: "Campaigns", resource: "campaign", action: "list" },
                  { label: "Related objects", resource: "related", action: "list" },
                ] as const
              ).map((tab) => (
                <Link
                  key={tab.resource}
                  prefetch={false}
                  href={partnerAccountHref({ ...tab, accountId: accountId! })}
                  aria-current={route.resource === tab.resource ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${route.resource === tab.resource ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                >
                  {tab.label}
                </Link>
              ))}
            </nav>
            {route.resource === "product" ? (
              <PartnerInventory account={data!.account} />
            ) : route.resource === "campaign" ? (
              <PartnerCampaigns account={data!.account} />
            ) : route.resource === "related" ? (
              <div className="space-y-6">
                <DataTable
                  label="Account members"
                  data={members}
                  columns={memberColumns}
                  getRowId={(row) => `${row.issuer}/${row.subject}`}
                  empty="No members added yet."
                  toolbar={
                    <Button asChild size="sm">
                      <Link
                        href={partnerAccountHref({
                          resource: "member",
                          action: "create",
                          accountId: accountId!,
                        })}
                        prefetch={false}
                      >
                        <Plus aria-hidden />
                        Add member
                      </Link>
                    </Button>
                  }
                />
                <DataTable
                  label="Placement reporting records"
                  data={data!.assets}
                  columns={assetColumns}
                  getRowId={(row) => row.id}
                  empty="No placement reporting records yet. Products and campaigns create these records automatically."
                  pagination={{
                    offset,
                    limit,
                    total: data!.asset_total,
                    onChange: (values) => {
                      setOffset(Number(values.offset));
                      if (values.limit) setLimit(Number(values.limit));
                    },
                  }}
                />
              </div>
            ) : (
              <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
                <div className="space-y-6">
                  <ResourcePanel
                    title="Partnership"
                    fields={[
                      { label: "Name", value: <DataValue value={data!.account.name} /> },
                      {
                        label: "Partnership tier",
                        value: (
                          <DataValue
                            value={
                              data!.account.tier.charAt(0).toUpperCase() +
                              data!.account.tier.slice(1)
                            }
                          />
                        ),
                      },
                      { label: "Status", value: <StatusBadge value={data!.account.status} /> },
                      { label: "Benefits", value: <DataValue value={data!.account.benefits} /> },
                    ]}
                  />
                  <ResourcePanel
                    title="Promotion defaults"
                    fields={[
                      {
                        label: "Default promotion",
                        value: `${data!.account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"} · ${data!.account.promotion_kind_source === "account" ? "account override" : "inherited from tier"}`,
                      },
                      {
                        label: "Product acceptance",
                        value:
                          data!.account.approval_mode === "trusted"
                            ? "Trusted account products accepted automatically"
                            : "Automatic capability checks",
                      },
                      {
                        label: "Product click destination",
                        value:
                          data!.account.outbound_destination === "listing_url"
                            ? "Partner listing · DevFeed UTM"
                            : "Product website · DevFeed UTM",
                      },
                      {
                        label: "Automatic ad formats",
                        value: (
                          <DataValue
                            value={(data!.account.ad_formats ?? ["feed"]).map(adFormatLabel)}
                          />
                        ),
                      },
                      {
                        label: "Ordinary recommendations",
                        value: <DataValue value={data!.account.organic_enabled ?? true} />,
                      },
                      {
                        label: "Automatic product inclusion",
                        value: <DataValue value={data!.account.auto_include_products ?? true} />,
                      },
                      {
                        label: "Allowed placements",
                        value: (
                          <DataValue
                            value={data!.account.promotion_surfaces ?? ["article", "topic", "feed"]}
                          />
                        ),
                      },
                      {
                        label: "Paid promotion",
                        value: (
                          <Link href={`${href}/campaigns`} className="text-primary hover:underline">
                            Manage agreed paid campaigns
                          </Link>
                        ),
                      },
                    ]}
                  >
                    <p className="py-3 text-sm text-muted-foreground">
                      Products added directly or imported inherit the account’s acceptance and
                      promotion policies. Sponsored defaults create an automatic standing campaign.
                      Contextual matches are preferred; sponsored feed placements can fill unmatched
                      feeds. Article and topic placements require a relevant match.
                    </p>
                  </ResourcePanel>
                  <PartnerAudience
                    accountId={data!.account.id}
                    value={audienceValue(data!.account.audience)}
                    enabled={!!data!.account.advanced_targeting}
                  />
                </div>
                <ResourcePanel
                  title="Record information"
                  fields={[
                    { label: "ID", value: <DataValue value={accountId} /> },
                    {
                      label: "Members",
                      value: (
                        <Link href={`${href}/related`} className="text-primary hover:underline">
                          {members.length}
                        </Link>
                      ),
                    },
                    {
                      label: "Placement reporting records",
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
