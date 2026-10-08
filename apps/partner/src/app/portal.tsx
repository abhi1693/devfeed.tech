"use client";

import { PortalShell } from "./portal-shell";
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
import { RefreshCw, Eye, MousePointerClick, TrendingUp } from "lucide-react";
import { useRouter } from "next/navigation";

import { useEffect, useState } from "react";
import type { AccountPage, Dashboard, Identity } from "@/lib/types";
import { Management } from "./management";

export function Portal({
  identity,
  initialAccounts,
}: {
  identity: Identity;
  initialAccounts: AccountPage;
}) {
  const router = useRouter();
  const [accounts, setAccounts] = useState(initialAccounts);
  const [accountOffset, setAccountOffset] = useState(0);
  const [selected, setSelected] = useState(initialAccounts.items[0]?.id ?? "");
  const [days, setDays] = useState(30);
  const [assetOffset, setAssetOffset] = useState(0);
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const superuser = identity.roles.includes("superuser");

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    fetch(`/api/v1/partner/accounts/${selected}/dashboard?days=${days}&offset=${assetOffset}`, {
      signal: controller.signal,
      cache: "no-store",
    })
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
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [selected, days, assetOffset, refresh, router]);

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
    else setError("Could not sign out. Please retry.");
  }
  function reload() {
    setData(null);
    setError("");
    setRefresh((value) => value + 1);
  }
  const number = (value: number) => value.toLocaleString("en-US");
  return (
    <PortalShell identity={identity} onSignOut={signOut}>
      <div id="overview" className="scroll-mt-6">
        <h1 className="text-2xl font-semibold tracking-tight">Partnership overview</h1>
        <p className="muted">Understand how your products and ads perform on DevFeed.</p>
        <div className="toolbar">
          <label>
            Partner account
            <select
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setAssetOffset(0);
                setData(null);
                setError("");
              }}
            >
              {accounts.items.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                  {account.status === "paused" ? " · Paused" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            Reporting period
            <select
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value));
                setData(null);
                setError("");
              }}
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>Last year</option>
            </select>
          </label>
          <Button variant="outline" onClick={reload}>
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
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
              {superuser
                ? "Create a partner account below to get started."
                : "Your account has portal access but no active partner membership. Contact the DevFeed team to connect your account."}
            </p>
          </section>
        )}
        {selected && !data && !error && <p role="status">Loading partnership performance…</p>}
        {data && (
          <>
            <section className="card partnership">
              <div>
                <div className="eyebrow">Partnership tier</div>
                <h2>{data.account.tier}</h2>
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
            <div id="performance" className="section-title scroll-mt-6">
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
              {data.totals.measured_days} of {days} days have measurements. These figures measure
              reach and traffic; revenue and conversions are not tracked.
            </p>
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
            <section className="card">
              <h2 id="assets" className="scroll-mt-6">
                Products & ads
              </h2>
              <p className="muted">Performance for assets associated with this partner account.</p>
              {data.assets.length ? (
                <div className="table-wrap">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Impressions</TableHead>
                        <TableHead>Clicks</TableHead>
                        <TableHead>CTR</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.assets.map((asset) => (
                        <TableRow key={asset.id}>
                          <TableCell>{asset.name}</TableCell>
                          <TableCell>{asset.kind === "ad" ? "Ad" : "Product"}</TableCell>
                          <TableCell>
                            <Badge variant={asset.status === "active" ? "success" : "neutral"}>
                              {asset.status}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {asset.measured_days ? number(asset.impressions) : "—"}
                          </TableCell>
                          <TableCell>{asset.measured_days ? number(asset.clicks) : "—"}</TableCell>
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
                    disabled={!assetOffset}
                    onClick={() => {
                      setAssetOffset(assetOffset - 100);
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
                    disabled={assetOffset + 100 >= data.asset_total}
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
          </>
        )}
        {superuser && (
          <Management
            identity={identity}
            account={data?.account ?? null}
            assets={data?.assets ?? []}
            onChange={() => {
              void loadAccounts(accountOffset);
              setError("");
              setRefresh((value) => value + 1);
            }}
          />
        )}
      </div>
    </PortalShell>
  );
}
