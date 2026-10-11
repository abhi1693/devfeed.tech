"use client";
import { portalPath } from "@/lib/routes";
import { Combobox } from "@devfeed/ui/combobox";
import { Select } from "@devfeed/ui/select";

import { PortalShell } from "./portal-shell";
import { PartnerPreferences } from "./partner-preferences";
import { Button } from "@/components/atoms/button";
import { Badge } from "@/components/atoms/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/atoms/table";
import {
  RefreshCw,
  Eye,
  MousePointerClick,
  TrendingUp,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
} from "lucide-react";
import { useRouter } from "next/navigation";

import { useEffect, useState } from "react";
import type { AccountPage, Dashboard, Identity, AssetSort, SortOrder } from "@/lib/types";

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
    : field === "name"
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
  initialSelected = "",
  initialDays = 30,
  initialAccountOffset = 0,
  initialAssetOffset = 0,
  initialSort = "name",
  initialOrder = "asc",
}: {
  identity: Identity;
  initialAccounts: AccountPage;
  section?: "overview" | "performance" | "assets";
  initialSelected?: string;
  initialDays?: number;
  initialAccountOffset?: number;
  initialAssetOffset?: number;
  initialSort?: AssetSort;
  initialOrder?: SortOrder;
}) {
  const router = useRouter();
  const [accounts, setAccounts] = useState(initialAccounts);
  const [accountOffset, setAccountOffset] = useState(initialAccountOffset);
  const [selected, setSelected] = useState(
    initialAccounts.total === 1
      ? (initialAccounts.items[0]?.id ?? "")
      : initialSelected || initialAccounts.items[0]?.id || "",
  );
  const [days, setDays] = useState(initialDays);
  const [assetOffset, setAssetOffset] = useState(initialAssetOffset);
  const [sort, setSort] = useState(initialSort);
  const [order, setOrder] = useState(initialOrder);
  const [sorting, setSorting] = useState(false);
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    fetch(
      `/api/v1/partner/accounts/${selected}/dashboard?days=${days}&offset=${assetOffset}&sort=${sort}&order=${order}`,
      {
        signal: controller.signal,
        cache: "no-store",
      },
    )
      .then(async (response) => {
        if (response.status === 401 || response.status === 403) {
          router.replace("/login");
          return null;
        }
        if (!response.ok)
          throw new Error(
            response.status === 404
              ? "This partner account is no longer available to you."
              : "Could not load performance. Please retry.",
          );
        return response.json() as Promise<Dashboard>;
      })
      .then((value) => {
        if (!controller.signal.aborted) {
          if (value && assetOffset > 0 && assetOffset >= value.asset_total) {
            setData(null);
            setAssetOffset(0);
            return;
          }
          setData(value);
          setSorting(false);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setData(null);
          setError(reason.message);
          setSorting(false);
        }
      });
    return () => controller.abort();
  }, [selected, days, assetOffset, sort, order, refresh, router]);

  async function loadAccounts(offset: number) {
    try {
      const response = await fetch(`/api/v1/partner/accounts?offset=${offset}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Could not load partner accounts.");
      const page = (await response.json()) as AccountPage;
      setAccounts(page);
      setAccountOffset(offset);
      if (!page.items.some((account) => account.id === selected)) {
        setSelected(page.items[0]?.id ?? "");
        setData(null);
        setAssetOffset(0);
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Please retry.");
    }
  }
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
    setOrder(next === sort ? (order === "asc" ? "desc" : "asc") : next === "name" ? "asc" : "desc");
    setSort(next);
    setAssetOffset(0);
    setSorting(true);
    setError("");
  }
  const scope = new URLSearchParams();
  scope.set("days", String(days));
  scope.set("sort", sort);
  scope.set("order", order);
  if (assetOffset) scope.set("asset_offset", String(assetOffset));
  if (accountOffset) scope.set("account_offset", String(accountOffset));
  const query = `?${scope}`;
  useEffect(() => {
    window.history.replaceState(
      window.history.state,
      "",
      `${portalPath(selected, section)}${query}`,
    );
  }, [query, selected, section]);
  const titles = {
    overview: ["Partnership overview", "Your partnership tier and included benefits."],
    performance: ["Performance", "Daily impressions and clicks for your selected account."],
    assets: ["Products & ads", "Performance for products and ads associated with your account."],
  };
  const number = (value: number) => value.toLocaleString("en-US");
  return (
    <PortalShell
      identity={identity}
      onSignOut={signOut}
      section={section}
      query={query}
      accountId={selected}
    >
      <div>
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{titles[section][0]}</h1>
            <p className="muted">{titles[section][1]}</p>
          </div>
          <Button variant="outline" onClick={reload}>
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
        {(accounts.total > 1 || section !== "overview") && (
          <div className="toolbar">
            {accounts.total > 1 && (
              <div className="grid w-full min-w-0 gap-2 sm:w-auto sm:min-w-48">
                <span className="text-sm font-medium">Partner account</span>
                <Combobox
                  label="Partner account"
                  required
                  value={selected}
                  onChange={(value) => {
                    if (value === selected) return;
                    setSelected(value);
                    setAssetOffset(0);
                    setData(null);
                    setError("");
                  }}
                  options={accounts.items.map((account) => ({
                    value: account.id,
                    label: account.name,
                    description: account.status === "paused" ? "Paused" : undefined,
                  }))}
                />
              </div>
            )}
            {section !== "overview" && (
              <div className="grid w-full min-w-0 gap-2 sm:w-auto sm:min-w-48">
                <span className="text-sm font-medium">Reporting period</span>
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
          </div>
        )}
        {accounts.total > 100 && (
          <nav aria-label="Account pages">
            <Button
              variant="outline"
              disabled={!accountOffset}
              onClick={() => loadAccounts(accountOffset - 100)}
            >
              Previous accounts
            </Button>
            <span>
              {accountOffset + 1}–{Math.min(accountOffset + 100, accounts.total)} of{" "}
              {accounts.total}
            </span>
            <Button
              variant="outline"
              disabled={accountOffset + 100 >= accounts.total}
              onClick={() => loadAccounts(accountOffset + 100)}
            >
              Next accounts
            </Button>
          </nav>
        )}
        {error && (
          <div role="alert" className="notice">
            {error}
            <Button variant="outline" onClick={reload}>
              Retry
            </Button>
          </div>
        )}
        {!accounts.total && (
          <section className="card">
            <h2>No partner accounts yet</h2>
            <p>
              Your account has portal access but no active partner membership. Contact the DevFeed
              team to connect your account.
            </p>
          </section>
        )}
        {selected && !data && !error && <p role="status">Loading partnership details…</p>}
        {data && (
          <>
            {section === "overview" && (
              <>
                <section className="card partnership">
                  <div>
                    <div className="eyebrow">Partnership tier</div>
                    <h2>
                      {data.account.tier.charAt(0).toUpperCase() + data.account.tier.slice(1)}
                    </h2>
                    <p>
                      {data.account.name}{" "}
                      <Badge variant={data.account.status === "active" ? "success" : "warning"}>
                        {data.account.status}
                      </Badge>
                    </p>
                  </div>
                  <div>
                    <h3>Your benefits</h3>
                    {data.account.benefits.length ? (
                      <ul>
                        {data.account.benefits.map((benefit, i) => (
                          <li key={i}>{benefit}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">Your partnership benefits have not been added yet.</p>
                    )}
                  </div>
                </section>
                <PartnerPreferences
                  key={data.account.id}
                  account={data.account}
                  csrfToken={identity.csrf_token}
                  onSaved={(account) =>
                    setData((current) =>
                      current?.account.id === account.id ? { ...current, account } : current,
                    )
                  }
                  onUnavailable={() => {
                    setData(null);
                    setError("This partner account is no longer available to you.");
                  }}
                />
              </>
            )}
            {section === "performance" && (
              <>
                <div className="section-title">
                  <h2>Performance</h2>
                  <span className="muted">
                    {data.start} to {data.end} · UTC
                  </span>
                </div>
                {!data.totals.measured_days && (
                  <p className="notice">
                    No delivery measurements for this period yet. Catalog matches are not counted as
                    impressions or clicks.
                  </p>
                )}
                <div className="metrics">
                  {[
                    [
                      "Impressions",
                      data.totals.measured_days ? number(data.totals.impressions) : "—",
                      "Recorded product and ad views",
                    ],
                    [
                      "Clicks",
                      data.totals.measured_days ? number(data.totals.clicks) : "—",
                      "Recorded visits to your destinations",
                    ],
                    [
                      "Click-through rate",
                      data.totals.ctr === null ? "—" : `${data.totals.ctr.toFixed(2)}%`,
                      "Clicks divided by impressions",
                    ],
                  ].map(([label, value, hint]) => (
                    <section className="card" key={label}>
                      <div className="flex items-center justify-between gap-2">
                        <h3>{label}</h3>
                        {label === "Impressions" ? (
                          <Eye className="size-4 text-muted-foreground" aria-hidden="true" />
                        ) : label === "Clicks" ? (
                          <MousePointerClick
                            className="size-4 text-muted-foreground"
                            aria-hidden="true"
                          />
                        ) : (
                          <TrendingUp className="size-4 text-muted-foreground" aria-hidden="true" />
                        )}
                      </div>
                      <strong className="metric-value">{value}</strong>
                      <p className="muted">{hint}</p>
                    </section>
                  ))}
                </div>
                <p className="muted">
                  {data.last_updated_at
                    ? `Last measurement update: ${new Date(data.last_updated_at).toISOString()}`
                    : "Awaiting first measurement."}{" "}
                  {data.totals.measured_days} of {days} days have measurements. These figures
                  measure reach and traffic; revenue and conversions are not tracked.
                </p>
              </>
            )}
            {section === "performance" && (
              <section className="card">
                <h2>Daily activity</h2>
                {data.trend.length ? (
                  <div className="table-wrap">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Date (UTC)</TableHead>
                          <TableHead>Impressions</TableHead>
                          <TableHead>Clicks</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.trend.map((row) => (
                          <TableRow key={row.day}>
                            <TableCell>{row.day}</TableCell>
                            <TableCell>{number(row.impressions)}</TableCell>
                            <TableCell>{number(row.clicks)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ) : (
                  <p className="muted">
                    Daily results appear when delivery measurements are available.
                  </p>
                )}
              </section>
            )}
            {section === "assets" && (
              <section className="card" aria-busy={sorting}>
                <h2 id="assets" className="scroll-mt-6">
                  Products & ads
                </h2>
                <p className="muted">
                  Performance for assets associated with this partner account.
                </p>
                {sorting && (
                  <p role="status" className="muted">
                    Updating products and ads…
                  </p>
                )}
                {data.assets.length ? (
                  <div className="table-wrap">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <AssetSortHeader
                            field="name"
                            label="Name"
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
                          <TableRow key={asset.id}>
                            <TableCell>{asset.name}</TableCell>
                            <TableCell>
                              <Badge variant={asset.status === "active" ? "success" : "neutral"}>
                                {asset.status}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              {asset.measured_days ? number(asset.impressions) : "—"}
                            </TableCell>
                            <TableCell>
                              {asset.measured_days ? number(asset.clicks) : "—"}
                            </TableCell>
                            <TableCell>
                              {asset.ctr === null ? "—" : `${asset.ctr.toFixed(2)}%`}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ) : (
                  <p>No products or ads are associated with this account yet.</p>
                )}
                {data.asset_total > 100 && (
                  <nav aria-label="Asset pages">
                    <Button
                      variant="outline"
                      disabled={sorting || !assetOffset}
                      onClick={() => {
                        setAssetOffset(Math.max(0, assetOffset - 100));
                        setData(null);
                      }}
                    >
                      Previous
                    </Button>
                    <span>
                      {assetOffset + 1}–{Math.min(assetOffset + 100, data.asset_total)} of{" "}
                      {data.asset_total}
                    </span>
                    <Button
                      variant="outline"
                      disabled={sorting || assetOffset + 100 >= data.asset_total}
                      onClick={() => {
                        setAssetOffset(assetOffset + 100);
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
