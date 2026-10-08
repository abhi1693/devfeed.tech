"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { AccountsOut, Dashboard, AdminIdentity } from "@/lib/api/generated/models";
import { PartnerAccountManagement } from "./partner-account-management";
import { Button } from "@/components/atoms/button";

export function PartnerAccounts({ identity }: { identity: AdminIdentity }) {
  const router = useRouter();
  const [accounts, setAccounts] = useState<AccountsOut>({ items: [], total: 0 });
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const [snapshot, setData] = useState<Dashboard | null>(null);
  const data = snapshot?.account.id === selected ? snapshot : null;
  const [assetOffset, setAssetOffset] = useState(0);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/v1/admin/partner-accounts?offset=${offset}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.status === 401 || response.status === 403) {
          router.replace("/login");
          return null;
        }
        if (!response.ok) throw new Error("Could not load partner accounts.");
        return response.json() as Promise<AccountsOut>;
      })
      .then((page) => {
        if (!controller.signal.aborted && page) {
          setAccounts(page);
          setSelected((current) =>
            page.items.some((a) => a.id === current) ? current : (page.items[0]?.id ?? ""),
          );
          setError("");
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [offset, revision, router]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    fetch(`/api/v1/admin/partner-accounts/${selected}/dashboard?offset=${assetOffset}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load partner account details.");
        return response.json() as Promise<Dashboard>;
      })
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setError("");
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [selected, revision, assetOffset]);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Partner accounts</h1>
        <p className="text-sm text-muted-foreground">
          Manage commercial partnerships, memberships, products, and ads.
        </p>
      </div>
      <label className="flex max-w-xl flex-col gap-2 text-sm font-medium">
        Partner account
        <select
          className="h-9 rounded-md border border-input bg-background px-3"
          value={selected}
          onChange={(e) => {
            if (e.target.value === selected) return;
            setSelected(e.target.value);
            setAssetOffset(0);
            setData(null);
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
      {accounts.total > 100 && (
        <nav aria-label="Account pages" className="flex items-center gap-3">
          <Button
            variant="outline"
            disabled={!offset}
            onClick={() => {
              setOffset(offset - 100);
              setAssetOffset(0);
            }}
          >
            Previous accounts
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + 100, accounts.total)} of {accounts.total}
          </span>
          <Button
            variant="outline"
            disabled={offset + 100 >= accounts.total}
            onClick={() => {
              setOffset(offset + 100);
              setAssetOffset(0);
            }}
          >
            Next accounts
          </Button>
        </nav>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {selected && !data && !error && <p role="status">Loading account details…</p>}
      <PartnerAccountManagement
        identity={identity}
        account={data?.account ?? null}
        assets={data?.assets ?? []}
        onChange={() => setRevision((value) => value + 1)}
      />
      {data && data.asset_total > 100 && (
        <nav aria-label="Asset pages" className="flex items-center gap-3">
          <Button
            variant="outline"
            disabled={!assetOffset}
            onClick={() => setAssetOffset(assetOffset - 100)}
          >
            Previous assets
          </Button>
          <span>
            {assetOffset + 1}–{Math.min(assetOffset + 100, data.asset_total)} of {data.asset_total}
          </span>
          <Button
            variant="outline"
            disabled={assetOffset + 100 >= data.asset_total}
            onClick={() => setAssetOffset(assetOffset + 100)}
          >
            Next assets
          </Button>
        </nav>
      )}
    </div>
  );
}
