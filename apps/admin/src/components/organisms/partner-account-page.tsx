"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Pencil, Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { InfoPanel, DataValue } from "@/components/molecules/info-panel";
import { audienceValue } from "@devfeed/ui/audience-targeting";
import { PartnerAudience } from "./partner-audience";
import { StatusBadge } from "@/components/molecules/status-badge";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { useAdmin } from "@/components/molecules/admin-session";
import type { Dashboard, AssetMetrics, MemberOut } from "@/lib/api/generated/models";
import { PartnerAccountManagement } from "./partner-account-management";
import { accountsPath, accountHref, accountsTrail } from "./partner-accounts";
import { PartnerCampaigns } from "./partner-campaigns";
import { PartnerCampaignPage } from "./partner-campaign-form";
import { PartnerInventory } from "./partner-inventory";
import { ApiError, returnToLogin } from "@/lib/api/client";

type Member = MemberOut;
export type AccountSection =
  | "details"
  | "edit"
  | "related"
  | "products"
  | "campaigns"
  | "campaigns/new"
  | `campaigns/${string}/edit`
  | "members/new"
  | "assets/new"
  | `assets/${string}/edit`;
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
  const campaignForm = section === "campaigns/new" || /^campaigns\/[^/]+\/edit$/.test(section);
  const asset = editingAsset
    ? data?.assets.find((item) => item.id === section.split("/")[1])
    : undefined;
  const form =
    !id ||
    section === "edit" ||
    section === "members/new" ||
    section === "assets/new" ||
    campaignForm ||
    editingAsset;
  const title = !id
    ? "Create partner account"
    : section === "edit"
      ? `Edit ${data?.account.name ?? "partner account"}`
      : section === "members/new"
        ? "Add member"
        : campaignForm
          ? section === "campaigns/new"
            ? "Create paid campaign"
            : "Edit paid campaign"
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
    {
      id: "association",
      header: "Association",
      cell: ({ row }) => (row.original.automatic ? "Automatic" : "Legacy manual asset"),
    },
    { accessorKey: "status", header: "Status", kind: "pill" },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <Button asChild variant="outline" size="sm">
          <Link
            href={
              row.original.automatic
                ? row.original.campaign_id
                  ? `${href}/campaigns/${row.original.campaign_id}/edit`
                  : `${href}/edit`
                : `${href}/assets/${row.original.id}/edit?offset=${offset}&limit=${limit}`
            }
            prefetch={false}
            aria-label={
              row.original.automatic
                ? `View policy for ${row.original.name}`
                : `Edit ${row.original.name}`
            }
          >
            {row.original.automatic ? "View policy" : "Edit"}
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
        (campaignForm && data ? (
          <PartnerCampaignPage
            account={data.account}
            campaignId={section === "campaigns/new" ? undefined : section.split("/")[1]}
          />
        ) : form ? (
          editingAsset && !asset ? (
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
              {(["details", "products", "campaigns", "related"] as const).map((tab) => (
                <Link
                  key={tab}
                  prefetch={false}
                  href={href + (tab === "details" ? "" : `/${tab}`)}
                  aria-current={section === tab ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${section === tab ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                >
                  {
                    {
                      details: "Details",
                      products: "Products",
                      campaigns: "Campaigns",
                      related: "Related objects",
                    }[tab]
                  }
                </Link>
              ))}
            </nav>
            {section === "products" ? (
              <PartnerInventory account={data!.account} />
            ) : section === "campaigns" ? (
              <PartnerCampaigns account={data!.account} />
            ) : section === "related" ? (
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
                  <InfoPanel
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
                  <InfoPanel
                    title="Promotion defaults"
                    fields={[
                      {
                        label: "Default promotion",
                        value: `${data!.account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"} · ${data!.account.promotion_kind_source === "account" ? "account override" : "inherited from tier"}`,
                      },
                      {
                        label: "Imported product acceptance",
                        value:
                          data!.account.approval_mode === "trusted"
                            ? "Trusted partner imports accepted automatically"
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
                      Imported products inherit the account’s acceptance and promotion policies.
                      Sponsored defaults create an automatic standing campaign. Contextual matches
                      are preferred; sponsored feed placements can fill unmatched feeds. Article and
                      topic placements require a relevant match.
                    </p>
                  </InfoPanel>
                  <PartnerAudience
                    accountId={data!.account.id}
                    value={audienceValue(data!.account.audience)}
                    enabled={!!data!.account.advanced_targeting}
                  />
                </div>
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
