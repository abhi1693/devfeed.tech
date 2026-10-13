"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Select } from "@devfeed/ui/select";
import { ResourceHeading, ResourcePanel } from "@devfeed/ui/resource-layout";
import { adFormatLabel } from "@devfeed/ui/advertising-formats";
import { RefreshCw, ArrowUp, ArrowDown, ArrowUpDown, ArrowRight } from "lucide-react";
import { PortalShell } from "./portal-shell";
import { PartnerAccountSettings } from "./partner-account-settings";
import { PartnerProductCatalog } from "./partner-product-catalog";
import { PartnerProductPage } from "./partner-product-page";
import { PartnerProductCreate } from "./partner-product-form";
import { PartnerConnections, type ConnectionsRoute } from "./partner-connections";
import { PartnerSettings } from "./partner-settings";
import { PartnerPerformance } from "./partner-performance";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@devfeed/ui/table";
import {
  accountSettingsPath,
  portalPath,
  productPath,
  type PortalSection,
  type AccountSettingsSection,
  type ProductRoute,
} from "@/lib/routes";
import { PartnerWorkspaceDashboardResponse } from "@/lib/api/generated/validators";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import type { Account, AccountPage, Identity, AssetSort, SortOrder } from "@/lib/types";
import type { WorkspaceDashboard } from "@/lib/api/generated/models";

function AssetSortHeader({
  field,
  label,
  sort,
  order,
  onSort,
}: {
  field: AssetSort;
  label: string;
  sort: AssetSort;
  order: SortOrder;
  onSort: (field: AssetSort) => void;
}) {
  const active = sort === field;
  const Icon = active ? (order === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  const nextOrder = active
    ? order === "asc"
      ? "descending"
      : "ascending"
    : field === "name" || field === "account"
      ? "ascending"
      : "descending";
  return (
    <TableHead
      scope="col"
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : undefined}
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ml-2"
        aria-label={`Sort by ${label}`}
        title={`Sort ${label.toLowerCase()} ${nextOrder}`}
        onClick={() => onSort(field)}
      >
        {label}
        <Icon aria-hidden="true" />
      </Button>
    </TableHead>
  );
}

export function Portal({
  identity,
  initialAccounts,
  section = "overview",
  settingsSection = "general",
  initialAccount,
  initialDays = 30,
  initialAccountOffset = 0,
  initialAssetOffset = 0,
  initialSort = "name",
  initialOrder = "asc",
  initialProductSearch = "",
  initialProductOffset = 0,
  initialIncludeArchived = false,
  initialPolicyOffset = 0,
  initialAccountSearch = "",
  productRoute = { view: "list" },
  connectionsRoute = { view: "list" },
}: {
  identity: Identity;
  initialAccounts: AccountPage;
  section?: PortalSection;
  settingsSection?: AccountSettingsSection;
  initialAccount?: Account;
  initialDays?: number;
  initialAccountOffset?: number;
  initialAssetOffset?: number;
  initialSort?: AssetSort;
  initialOrder?: SortOrder;
  initialProductSearch?: string;
  initialProductOffset?: number;
  initialIncludeArchived?: boolean;
  initialPolicyOffset?: number;
  initialAccountSearch?: string;
  productRoute?: ProductRoute;
  connectionsRoute?: ConnectionsRoute;
}) {
  const router = useRouter();
  const [account, setAccount] = useState(initialAccount);
  const [days, setDays] = useState(initialDays);
  const [assetOffset, setAssetOffset] = useState(initialAssetOffset);
  const [sort, setSort] = useState(initialSort);
  const [order, setOrder] = useState(initialOrder);
  const [sorting, setSorting] = useState(false);
  const [data, setData] = useState<WorkspaceDashboard | null>(null);
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const reporting = section === "overview" || section === "performance";
  const request = usePartnerRequest({
    onUnavailable: () => {
      setData(null);
      setUnavailable(true);
    },
  });
  function onUnavailable() {
    setData(null);
    setUnavailable(true);
    setError(
      account
        ? "This partner account is no longer available to you."
        : "Your partner access is no longer available. Sign in again.",
    );
  }
  useEffect(() => {
    if (!reporting || initialAccounts.total === 0) return;
    const controller = new AbortController();
    const params = new URLSearchParams({
      days: String(days),
      limit: "25",
      offset: String(assetOffset),
      sort,
      order,
    });
    if (account) params.set("account_id", account.id);
    void request(`dashboard?${params}`, {
      signal: controller.signal,
      parse: (value) => PartnerWorkspaceDashboardResponse.parse(value),
      errorMessage: "Could not load performance. Please retry.",
      invalidMessage: "Could not confirm workspace performance. Please retry.",
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (assetOffset > 0 && assetOffset >= value.asset_total) {
          setData(null);
          setAssetOffset(0);
          return;
        }
        setData(value);
        setSorting(false);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason)) {
          setData(null);
          setError(
            reason instanceof Error ? reason.message : "Could not load performance. Please retry.",
          );
          setSorting(false);
        }
      });
    return () => controller.abort();
  }, [request, reporting, initialAccounts.total, account, days, assetOffset, sort, order, refresh]);
  async function signOut() {
    const response = await fetch("/api/v1/partner/auth/logout", {
      method: "POST",
      headers: { "x-csrf-token": identity.csrf_token },
    });
    if (response.ok) router.replace("/login");
    else throw new Error("Could not sign out. Please retry.");
  }
  function reload() {
    setData(null);
    setError("");
    setRefresh((value) => value + 1);
  }
  function changeSort(next: AssetSort) {
    setOrder(
      next === sort
        ? order === "asc"
          ? "desc"
          : "asc"
        : next === "name" || next === "account"
          ? "asc"
          : "desc",
    );
    setSort(next);
    setAssetOffset(0);
    setSorting(true);
    setError("");
  }
  const scope = new URLSearchParams({ days: String(days), sort, order });
  if (assetOffset) scope.set("asset_offset", String(assetOffset));
  const query = `?${scope}`;
  const productScope = new URLSearchParams(scope);
  if (initialProductSearch) productScope.set("q", initialProductSearch);
  if (initialProductOffset) productScope.set("product_offset", String(initialProductOffset));
  if (initialIncludeArchived) productScope.set("include_archived", "true");
  if (initialPolicyOffset) productScope.set("policy_offset", String(initialPolicyOffset));
  const productQuery = `?${productScope}`;
  const settingsScope = new URLSearchParams();
  if (initialAccountSearch) settingsScope.set("q", initialAccountSearch);
  if (initialAccountOffset) settingsScope.set("account_offset", String(initialAccountOffset));
  const settingsQuery = settingsScope.size ? `?${settingsScope}` : "";
  useEffect(() => {
    if (reporting) {
      const params = new URLSearchParams(query);
      if (account) params.set("account_id", account.id);
      window.history.replaceState(window.history.state, "", `${portalPath(section)}?${params}`);
    }
  }, [query, reporting, section, account]);
  const number = (value: number) => value.toLocaleString("en-US");
  return (
    <PortalShell identity={identity} onSignOut={signOut} section={section} query={query}>
      <div className="space-y-4">
        {reporting && (
          <ResourceHeading
            homeHref={portalPath()}
            homeLabel="Dashboard"
            siteTitle="DevFeed Partners"
            title={section === "overview" ? "Dashboard" : "Performance"}
            description={
              section === "overview"
                ? "Your native advertising workspace across all partner accounts."
                : "Track reach and visits across every partner account and native ad format."
            }
          >
            {initialAccounts.total > 0 && (
              <div className="w-40">
                <span className="sr-only">Reporting period</span>
                <Select
                  label="Reporting period"
                  required
                  value={String(days)}
                  onChange={(value) => {
                    if (Number(value) === days) return;
                    setDays(Number(value));
                    setAssetOffset(0);
                    setData(null);
                    setError("");
                  }}
                  options={[
                    { value: "7", label: "Last 7 days" },
                    { value: "30", label: "Last 30 days" },
                    { value: "90", label: "Last 90 days" },
                    { value: "365", label: "Last year" },
                  ]}
                />
              </div>
            )}
            <Button variant="outline" onClick={reload}>
              <RefreshCw aria-hidden />
              Refresh
            </Button>
          </ResourceHeading>
        )}
        {reporting && initialAccounts.total > 0 && account && (
          <p className="text-sm text-muted-foreground">
            Filtered to {account.name}.{" "}
            <Link href={`${portalPath(section)}${query}`} className="text-primary hover:underline">
              Show all partner accounts
            </Link>
          </p>
        )}
        {error && (
          <div role="alert" className="notice">
            {error}
            {reporting && !unavailable && (
              <Button variant="outline" onClick={reload}>
                Retry
              </Button>
            )}
          </div>
        )}
        {!initialAccounts.total && reporting && (
          <ResourcePanel title="No partner accounts yet">
            <p className="text-sm text-muted-foreground">
              Your account has portal access but no active partner membership. Contact the DevFeed
              team to connect your account.
            </p>
          </ResourcePanel>
        )}
        {reporting && initialAccounts.total > 0 && !data && !error && !unavailable && (
          <p role="status">Loading workspace performance…</p>
        )}
        {!unavailable &&
          section === "products" &&
          (productRoute.view === "list" ? (
            <PartnerProductCatalog
              onUnavailable={onUnavailable}
              initialSearch={initialProductSearch}
              initialOffset={initialProductOffset}
              initialIncludeArchived={initialIncludeArchived}
              query={query}
              accountFilter={account}
            />
          ) : productRoute.view === "create" ? (
            initialAccounts.total ? (
              <PartnerProductCreate
                account={account}
                csrfToken={identity.csrf_token}
                onUnavailable={onUnavailable}
                query={productQuery}
              />
            ) : (
              <ResourcePanel title="Partner account required">
                <p className="text-sm text-muted-foreground">
                  An active partner account is required. Contact DevFeed to set up your
                  organization.
                </p>
              </ResourcePanel>
            )
          ) : (
            productRoute.productId && (
              <PartnerProductPage
                key={`${account?.id ?? "all"}/${productRoute.productId}/${productRoute.view}`}
                account={account}
                productId={productRoute.productId}
                edit={productRoute.view === "placements"}
                action={
                  productRoute.view === "edit" || productRoute.view === "archive"
                    ? productRoute.view
                    : undefined
                }
                contextOffset={initialPolicyOffset}
                csrfToken={identity.csrf_token}
                query={productQuery}
                onUnavailable={onUnavailable}
              />
            )
          ))}
        {!unavailable && section === "connections" && (
          <PartnerConnections
            key={`${connectionsRoute.view}/${connectionsRoute.connectionId ?? ""}/${connectionsRoute.q ?? ""}`}
            csrfToken={identity.csrf_token}
            onUnavailable={onUnavailable}
            route={connectionsRoute}
          />
        )}
        {!unavailable &&
          section === "settings" &&
          (account ? (
            <PartnerAccountSettings
              account={account}
              section={settingsSection}
              query={settingsQuery}
              csrfToken={identity.csrf_token}
              onSaved={setAccount}
              onUnavailable={onUnavailable}
            />
          ) : (
            <PartnerSettings
              initialAccounts={initialAccounts}
              initialSearch={initialAccountSearch}
              initialOffset={initialAccountOffset}
              onUnavailable={onUnavailable}
            />
          ))}
        {data && reporting && (
          <>
            <PartnerPerformance
              data={data}
              compact={section === "overview"}
              accountId={account?.id}
            />
            {section === "overview" && (
              <nav
                aria-label="Workspace shortcuts"
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 text-sm"
              >
                <p className="text-muted-foreground">
                  {number(data.account_total)} partner{" "}
                  {data.account_total === 1 ? "account" : "accounts"}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {(["products", "connections", "settings"] as const).map((section) => (
                    <Link
                      key={section}
                      href={portalPath(section)}
                      prefetch={false}
                      className="inline-flex items-center gap-1.5 text-primary hover:underline"
                    >
                      {section === "products"
                        ? "Manage products"
                        : section === "connections"
                          ? "Manage connections"
                          : "Account settings"}
                      <ArrowRight aria-hidden className="size-4" />
                    </Link>
                  ))}
                </div>
              </nav>
            )}
            {section === "performance" && (
              <section
                className="space-y-4"
                aria-label="Product and ad performance"
                aria-busy={sorting}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="text-base font-semibold">Product and ad performance</h2>
                  <Link
                    href={`${portalPath("products")}${query}`}
                    prefetch={false}
                    className="text-sm text-primary hover:underline"
                  >
                    Manage products
                  </Link>
                </div>
                {sorting && (
                  <p role="status" className="muted">
                    Updating products and ads…
                  </p>
                )}
                {data.assets.length ? (
                  <div className="overflow-x-auto rounded-md border">
                    <Table aria-label="Product and ad results">
                      <TableHeader>
                        <TableRow>
                          <AssetSortHeader
                            field="name"
                            label="Name"
                            sort={sort}
                            order={order}
                            onSort={changeSort}
                          />
                          <AssetSortHeader
                            field="account"
                            label="Partner account"
                            sort={sort}
                            order={order}
                            onSort={changeSort}
                          />
                          <TableHead>Status</TableHead>
                          {(["impressions", "clicks", "ctr"] as const).map((field) => (
                            <AssetSortHeader
                              key={field}
                              field={field}
                              label={
                                field === "ctr"
                                  ? "CTR"
                                  : field === "clicks"
                                    ? "Clicks"
                                    : "Impressions"
                              }
                              sort={sort}
                              order={order}
                              onSort={changeSort}
                            />
                          ))}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.assets.map((asset) => (
                          <TableRow key={`${asset.account_id}/${asset.id}`}>
                            <TableCell>
                              <span data-asset-name>
                                {asset.product_id ? (
                                  <Link
                                    href={productPath(asset.product_id, {
                                      accountId: asset.account_id,
                                      query,
                                    })}
                                    prefetch={false}
                                    className="font-medium text-primary hover:underline"
                                  >
                                    {asset.name}
                                  </Link>
                                ) : (
                                  asset.name
                                )}
                              </span>
                              {(asset.ad_formats?.length || asset.ad_format) && (
                                <span className="mt-1 block text-xs text-muted-foreground">
                                  {(asset.ad_formats ?? [asset.ad_format!])
                                    .map(adFormatLabel)
                                    .join(" · ")}
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              <Link
                                href={accountSettingsPath(asset.account_id)}
                                prefetch={false}
                                className="text-primary hover:underline"
                              >
                                {asset.account_name}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <Badge variant={asset.status === "active" ? "success" : "neutral"}>
                                {asset.status}
                              </Badge>
                            </TableCell>
                            <TableCell className="tabular-nums">
                              {asset.measured_days ? number(asset.impressions) : "—"}
                            </TableCell>
                            <TableCell className="tabular-nums">
                              {asset.measured_days ? number(asset.clicks) : "—"}
                            </TableCell>
                            <TableCell className="tabular-nums">
                              {asset.ctr === null ? "—" : `${asset.ctr.toFixed(2)}%`}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>{" "}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No product or ad results are available for this period yet.
                  </p>
                )}
                {data.asset_total > 25 && (
                  <nav aria-label="Asset pages">
                    <Button
                      variant="outline"
                      disabled={sorting || !assetOffset}
                      onClick={() => {
                        setAssetOffset(Math.max(0, assetOffset - 25));
                        setData(null);
                      }}
                    >
                      Previous
                    </Button>
                    <span>
                      {assetOffset + 1}–{Math.min(assetOffset + 25, data.asset_total)} of{" "}
                      {data.asset_total}
                    </span>
                    <Button
                      variant="outline"
                      disabled={sorting || assetOffset + 25 >= data.asset_total}
                      onClick={() => {
                        setAssetOffset(assetOffset + 25);
                        setData(null);
                      }}
                    >
                      Next
                    </Button>
                  </nav>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </PortalShell>
  );
}
