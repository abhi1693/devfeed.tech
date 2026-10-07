"use client";

import Link from "next/link";
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
    <div className="shell">
      <header>
        <Link className="brand" href="/">
          DevFeed <span>Partners</span>
        </Link>
        <div className="header-actions">
          <span>{identity.name ?? "Partner"}</span>
          {superuser && <span className="badge">Superuser</span>}
          <button onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main>
        <div className="eyebrow">Your partnership, in focus</div>
        <h1>Partnership overview</h1>
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
          <button onClick={reload}>Refresh</button>
        </div>
        {accounts.total > 100 && (
          <nav aria-label="Account pages">
            <button disabled={!accountOffset} onClick={() => loadAccounts(accountOffset - 100)}>
              Previous accounts
            </button>
            <span>
              {accountOffset + 1}–{Math.min(accountOffset + 100, accounts.total)} of{" "}
              {accounts.total}
            </span>
            <button
              disabled={accountOffset + 100 >= accounts.total}
              onClick={() => loadAccounts(accountOffset + 100)}
            >
              Next accounts
            </button>
          </nav>
        )}
        {error && (
          <div role="alert" className="notice">
            {error}
            <button onClick={reload}>Retry</button>
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
                  {data.account.name} <span className="badge">{data.account.status}</span>
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
                  <h3>{label}</h3>
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
                  <table>
                    <thead>
                      <tr>
                        <th>Date (UTC)</th>
                        <th>Impressions</th>
                        <th>Clicks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.trend.map((row) => (
                        <tr key={row.day}>
                          <td>{row.day}</td>
                          <td>{number(row.impressions)}</td>
                          <td>{number(row.clicks)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted">
                  Daily results appear when delivery measurements are available.
                </p>
              )}
            </section>
            <section className="card">
              <h2>Products & ads</h2>
              <p className="muted">Performance for assets associated with this partner account.</p>
              {data.assets.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Impressions</th>
                        <th>Clicks</th>
                        <th>CTR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.assets.map((asset) => (
                        <tr key={asset.id}>
                          <td>{asset.name}</td>
                          <td>{asset.kind === "ad" ? "Ad" : "Product"}</td>
                          <td>
                            <span className="badge">{asset.status}</span>
                          </td>
                          <td>{asset.measured_days ? number(asset.impressions) : "—"}</td>
                          <td>{asset.measured_days ? number(asset.clicks) : "—"}</td>
                          <td>{asset.ctr === null ? "—" : `${asset.ctr.toFixed(2)}%`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p>No products or ads are associated with this account yet.</p>
              )}
              {data.asset_total > 100 && (
                <nav aria-label="Asset pages">
                  <button
                    disabled={!assetOffset}
                    onClick={() => {
                      setAssetOffset(assetOffset - 100);
                      setData(null);
                    }}
                  >
                    Previous
                  </button>
                  <span>
                    {assetOffset + 1}–{Math.min(assetOffset + 100, data.asset_total)} of{" "}
                    {data.asset_total}
                  </span>
                  <button
                    disabled={assetOffset + 100 >= data.asset_total}
                    onClick={() => {
                      setAssetOffset(assetOffset + 100);
                      setData(null);
                    }}
                  >
                    Next
                  </button>
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
      </main>
      <footer>DevFeed Partners · Measured outcomes, transparent partnerships.</footer>
    </div>
  );
}
