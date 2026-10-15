"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { PartnerPerformance } from "@devfeed/ui/partner-performance";
import { metricCount, metricPercent } from "@devfeed/ui/performance";
import { adFormatLabel } from "@devfeed/ui/advertising-formats";
import { Select } from "@devfeed/ui/select";
import { RequestState } from "@/components/molecules/request-state";
import { StatusBadge } from "@/components/molecules/status-badge";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { useAdmin } from "@/components/molecules/admin-session";
import type { AssetMetrics, MemberOut } from "@/lib/api/generated/models";
import { PartnerAccountManagement, type AccountSettingsTab } from "./partner-account-management";
import { PartnerAccountOverview } from "./partner-account-overview";
import {
  accountsPath,
  accountsTrail,
  partnerAccountHref,
  type PartnerAccountRoute,
} from "@/lib/partner-account-routes";
import { PartnerCampaigns } from "./partner-campaigns";
import { PartnerCampaignPage } from "./partner-campaign-form";
import { PartnerInventory } from "./partner-inventory";
import { ApiError, returnToLogin } from "@/lib/api/client";
import {
  adminPartnerAccountGet,
  adminPartnerAssetGet,
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet,
  membersV1AdminPartnerAccountsAccountIdMembersGet,
  removeMemberV1AdminPartnerAccountsAccountIdMembersSubjectDelete,
} from "@/lib/api/generated/admin";
import { useRequest } from "@/lib/use-request";

type Member = MemberOut;
const sections = [
  { label: "Overview", key: "overview", resource: "account", action: "view" },
  { label: "Performance", key: "performance", resource: "performance", action: "list" },
  { label: "Products", key: "product", resource: "product", action: "list" },
  { label: "Campaigns", key: "campaign", resource: "campaign", action: "list" },
  { label: "Members", key: "member", resource: "member", action: "list" },
  { label: "Settings", key: "settings", resource: "account", action: "edit" },
] as const;

export function PartnerAccountPage({
  route,
  initialOffset = 0,
  initialLimit = 100,
  initialDays = 30,
  initialSettingsTab = "general",
}: {
  route: PartnerAccountRoute;
  initialOffset?: number;
  initialLimit?: number;
  initialDays?: number;
  initialSettingsTab?: AccountSettingsTab;
}) {
  const admin = useAdmin();
  const accountId =
    route.resource === "account" && route.action === "create" ? undefined : route.accountId;
  const [accountRevision, setAccountRevision] = useState(0);
  const [reportRevision, setReportRevision] = useState(0);
  const [memberRevision, setMemberRevision] = useState(0);
  const [assetRevision, setAssetRevision] = useState(0);
  const [memberError, setMemberError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(initialOffset);
  const [limit, setLimit] = useState(initialLimit);
  const [days, setDays] = useState(initialDays);
  function updateReportUrl(values: Record<string, string>) {
    const query = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(values)) query.set(key, value);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${query}`);
  }
  const overview = route.resource === "account" && route.action === "view";
  const performance = route.resource === "performance";
  const reporting = overview || performance;
  const memberList = route.resource === "member" && route.action === "list";
  const editingSettings = route.resource === "account" && route.action === "edit";
  const assetId =
    route.resource === "asset" && route.action === "edit" ? route.identifier : undefined;
  const reportOffset = performance ? offset : 0;
  const reportLimit = performance ? limit : 1;
  const loadAccount = useCallback(
    async (signal: AbortSignal) =>
      accountId ? adminPartnerAccountGet(accountId, { signal }) : null,
    [accountId],
  );
  const accountResult = useRequest(`partner-account/${accountId}/${accountRevision}`, loadAccount);
  const account = accountResult.data;
  const loadReport = useCallback(
    async (signal: AbortSignal) =>
      accountId && reporting
        ? dashboardV1AdminPartnerAccountsAccountIdDashboardGet(
            accountId,
            { days, offset: reportOffset, limit: reportLimit },
            { signal },
          )
        : null,
    [accountId, reporting, days, reportOffset, reportLimit],
  );
  const reportResult = useRequest(
    `partner-report/${accountId}/${reporting}/${days}/${reportOffset}/${reportLimit}/${reportRevision}`,
    loadReport,
  );
  const report = reportResult.data;
  const loadMembers = useCallback(
    async (signal: AbortSignal) =>
      accountId && memberList
        ? membersV1AdminPartnerAccountsAccountIdMembersGet(accountId, { signal })
        : [],
    [accountId, memberList],
  );
  const membersResult = useRequest(
    `partner-members/${accountId}/${memberList}/${memberRevision}`,
    loadMembers,
  );
  const members = membersResult.data ?? [];
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
  const editingAsset = route.resource === "asset" && route.action === "edit";
  const campaignForm = route.resource === "campaign" && route.action !== "list";
  const form = route.action === "create" || route.action === "edit";
  const activeSection =
    route.resource === "account"
      ? editingSettings
        ? "settings"
        : "overview"
      : route.resource === "asset"
        ? "performance"
        : route.resource;
  const title =
    route.resource === "account" && route.action === "create"
      ? "Create partner account"
      : route.resource === "member" && route.action === "create"
        ? "Add member"
        : campaignForm
          ? route.action === "create"
            ? "Create paid campaign"
            : "Edit paid campaign"
          : route.resource === "asset" && route.action === "create"
            ? "Associate asset"
            : editingAsset
              ? `Edit ${asset?.name ?? "asset"}`
              : (account?.name ?? "Partner account");
  async function removeMember(subject: string) {
    if (!accountId) return;
    setBusy(true);
    setMemberError(undefined);
    try {
      await removeMemberV1AdminPartnerAccountsAccountIdMembersSubjectDelete(
        accountId,
        encodeURIComponent(subject),
        {
          headers: { "x-csrf-token": admin.csrf_token },
        },
      );
      setMemberRevision((value) => value + 1);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) returnToLogin();
      else
        setMemberError(
          reason instanceof Error ? reason : new Error("Could not remove membership."),
        );
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
            : "Manual",
    },
    { accessorKey: "status", header: "Status", kind: "pill" },
    {
      accessorKey: "impressions",
      header: "Impressions",
      cell: ({ row }) => (row.original.measured_days ? metricCount(row.original.impressions) : "—"),
    },
    {
      accessorKey: "clicks",
      header: "Clicks",
      cell: ({ row }) => (row.original.measured_days ? metricCount(row.original.clicks) : "—"),
    },
    {
      accessorKey: "ctr",
      header: "CTR",
      cell: ({ row }) => (row.original.ctr === null ? "—" : metricPercent(row.original.ctr)),
    },
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
                    })}?offset=${offset}&limit=${limit}&days=${days}`
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
    <section className="min-w-0 space-y-4">
      <ResourceHeading
        title={title}
        trail={[
          ...accountsTrail,
          { label: "Partner accounts", href: accountsPath },
          ...(form && !editingSettings && account
            ? [
                {
                  label: account.name,
                  href: partnerAccountHref({
                    resource: "account",
                    action: "view",
                    accountId: account.id,
                  }),
                },
              ]
            : []),
        ]}
      >
        {account && !form && (
          <Button asChild variant="outline" size="sm">
            <Link
              href={partnerAccountHref({
                resource: "account",
                action: "edit",
                accountId: account.id,
              })}
              prefetch={false}
            >
              Edit account
            </Link>
          </Button>
        )}
      </ResourceHeading>
      <RequestState
        loading={!!accountId && accountResult.loading}
        error={accountResult.error}
        retry={() => setAccountRevision((value) => value + 1)}
      />
      {(!accountId || account) && (
        <>
          {account && (
            <>
              <div
                aria-label="Account status"
                className="flex flex-wrap items-center gap-2 text-xs"
              >
                <StatusBadge value={account.status ?? "active"} />
                <Badge variant="secondary" className="capitalize">
                  {account.tier}
                </Badge>
                <span className="text-muted-foreground">
                  {account.effective_promotion_kind === "ad"
                    ? "Sponsored"
                    : "Product recommendations"}
                </span>
              </div>
              <nav
                aria-label="Account sections"
                className="flex min-w-0 flex-wrap gap-x-5 border-b sm:flex-nowrap sm:overflow-x-auto"
              >
                {sections.map((section) => (
                  <Link
                    key={section.key}
                    prefetch={false}
                    href={partnerAccountHref({
                      ...section,
                      accountId: account.id,
                    })}
                    aria-current={activeSection === section.key ? "page" : undefined}
                    className={`shrink-0 whitespace-nowrap border-b-2 px-1 py-2.5 text-sm ${activeSection === section.key ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                  >
                    {section.label}
                  </Link>
                ))}
              </nav>
            </>
          )}
          {campaignForm && account ? (
            <PartnerCampaignPage
              account={account}
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
                This asset is unavailable. Return to Performance to select a reporting record.
              </p>
            ) : editingAsset && asset?.automatic ? (
              <p role="alert">
                This reporting record is managed automatically. Change promotion settings in the
                account defaults or its paid campaign.
              </p>
            ) : (
              <div className="max-w-4xl">
                <PartnerAccountManagement
                  key={partnerAccountHref(route)}
                  account={account ?? undefined}
                  asset={asset}
                  mode={
                    route.resource === "member"
                      ? "member"
                      : route.resource === "asset"
                        ? "asset"
                        : "account"
                  }
                  initialTab={initialSettingsTab}
                />
              </div>
            )
          ) : (
            account && (
              <>
                {reporting && (
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <h2 className="text-base font-semibold">
                          {performance ? "Performance" : "Overview"}
                        </h2>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {performance
                            ? "Delivery measurements and placement records for this account."
                            : "Account delivery and promotion defaults at a glance."}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 text-sm">
                        <label
                          htmlFor="account-report-period"
                          className="text-xs text-muted-foreground"
                        >
                          Period
                        </label>
                        <Select
                          id="account-report-period"
                          label="Reporting period"
                          value={String(days)}
                          onChange={(value) => {
                            setDays(Number(value));
                            setOffset(0);
                            updateReportUrl({ days: value, offset: "0" });
                          }}
                          options={[
                            { value: "7", label: "Last 7 days" },
                            { value: "30", label: "Last 30 days" },
                            { value: "90", label: "Last 90 days" },
                          ]}
                          className="w-40"
                        />
                      </div>
                    </div>
                    <RequestState
                      loading={reportResult.loading}
                      error={reportResult.error}
                      retry={() => setReportRevision((value) => value + 1)}
                    />
                    {report && (
                      <PartnerPerformance
                        data={report}
                        compact={overview}
                        performanceHref={`${partnerAccountHref({ resource: "performance", action: "list", accountId: account.id })}?days=${days}`}
                      />
                    )}
                    {overview && <PartnerAccountOverview account={account} />}
                    {performance && report && (
                      <DataTable
                        label="Placement reporting records"
                        data={report?.assets ?? []}
                        columns={assetColumns}
                        getRowId={(row) => row.id}
                        empty="No placement reporting records yet. Products and campaigns create these records automatically."
                        pagination={{
                          offset,
                          limit,
                          total: report?.asset_total ?? 0,
                          onChange: (values) => {
                            setOffset(Number(values.offset));
                            if (values.limit) setLimit(Number(values.limit));
                            updateReportUrl({ ...values, days: String(days) });
                          },
                        }}
                      />
                    )}
                  </div>
                )}
                {route.resource === "product" && <PartnerInventory account={account} />}
                {route.resource === "campaign" && <PartnerCampaigns account={account} />}
                {memberList && (
                  <div className="space-y-3">
                    <div>
                      <h2 className="text-base font-semibold">Members</h2>
                      <p className="mt-1 text-sm text-muted-foreground">
                        People who can manage this account in the partner portal.
                      </p>
                    </div>
                    <RequestState
                      error={memberError}
                      retry={() => {
                        setMemberError(undefined);
                        setMemberRevision((value) => value + 1);
                      }}
                    />
                    <DataTable
                      label="Account members"
                      data={members}
                      columns={memberColumns}
                      getRowId={(row) => `${row.issuer}/${row.subject}`}
                      loading={membersResult.loading}
                      error={membersResult.error}
                      onRetry={() => setMemberRevision((value) => value + 1)}
                      empty="No members added yet."
                      toolbar={
                        <Button asChild size="sm">
                          <Link
                            href={partnerAccountHref({
                              resource: "member",
                              action: "create",
                              accountId: account.id,
                            })}
                            prefetch={false}
                          >
                            <Plus aria-hidden />
                            Add member
                          </Link>
                        </Button>
                      }
                    />
                  </div>
                )}
              </>
            )
          )}
        </>
      )}
    </section>
  );
}
