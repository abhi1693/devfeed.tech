"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Pencil, RefreshCw } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Input } from "@devfeed/ui/input";
import { Badge } from "@devfeed/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@devfeed/ui/table";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { adFormatLabel } from "@devfeed/ui/advertising-formats";
import { accountSettingsPath, portalPath } from "@/lib/routes";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import { PartnerAccountsListResponse } from "@/lib/api/generated/validators";
import type { AccountPage } from "@/lib/types";

export function PartnerSettings({
  initialAccounts,
  initialOffset = 0,
  initialSearch = "",
  onUnavailable,
}: {
  initialAccounts: AccountPage;
  initialOffset?: number;
  initialSearch?: string;
  onUnavailable: () => void;
}) {
  const request = usePartnerRequest({ onUnavailable });
  const [search, setSearch] = useState(initialSearch);
  const [page, setPage] = useState({ offset: initialOffset, q: initialSearch });
  const [revision, setRevision] = useState(0);
  const key = `${page.q}/${page.offset}/${revision}`;
  const initialKey = `${initialSearch}/${initialOffset}/0`;
  const initialPageUsed = useRef(false);
  const [result, setResult] = useState({ key: initialKey, ...initialAccounts, error: "" });
  const loading = result.key !== key || search.trim() !== page.q;
  const scope = new URLSearchParams();
  if (page.q) scope.set("q", page.q);
  if (page.offset) scope.set("account_offset", String(page.offset));
  const query = scope.size ? `?${scope}` : "";
  useEffect(() => {
    if (search.trim() === page.q) return;
    const timer = setTimeout(() => setPage({ q: search.trim(), offset: 0 }), 250);
    return () => clearTimeout(timer);
  }, [search, page.q]);
  useEffect(() => {
    window.history.replaceState(window.history.state, "", `${portalPath("settings")}${query}`);
  }, [query]);
  useEffect(() => {
    // The server already authorized and loaded the initial account page.
    if (!initialPageUsed.current) {
      initialPageUsed.current = true;
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: String(page.offset), limit: "25", q: page.q });
    void request(`accounts?${params}`, {
      signal: controller.signal,
      parse: (value) => PartnerAccountsListResponse.parse(value),
      errorMessage: "Could not load partner account settings. Please retry.",
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (page.offset > 0 && page.offset >= value.total) {
          setPage((current) => ({ ...current, offset: 0 }));
          return;
        }
        setResult({ key, ...value, error: "" });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setResult({
            key,
            items: [],
            total: 0,
            error:
              reason instanceof Error
                ? reason.message
                : "Could not load partner account settings. Please retry.",
          });
      });
    return () => controller.abort();
  }, [request, page, key, initialKey]);
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title="Settings"
        description="Review delivery defaults and audience settings for each of your partner accounts."
      >
        <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
          <RefreshCw aria-hidden />
          Refresh
        </Button>
      </ResourceHeading>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid w-full gap-1.5 sm:max-w-sm">
          <label htmlFor="settings-account-search" className="text-sm font-medium">
            Search partner accounts
          </label>
          <Input
            id="settings-account-search"
            type="search"
            maxLength={200}
            value={search}
            placeholder="Search partner accounts…"
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <p role="status" className="text-sm text-muted-foreground">
          {loading
            ? "Loading partner accounts…"
            : `${result.total.toLocaleString("en-US")} partner ${result.total === 1 ? "account" : "accounts"}`}
        </p>
      </div>
      {result.error && (
        <div role="alert" className="notice">
          <p>{result.error}</p>
          <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
            Retry partner accounts
          </Button>
        </div>
      )}
      <div className="overflow-x-auto rounded-md border" aria-busy={loading}>
        <Table aria-label="Partner accounts" className="[&_td]:py-1.5">
          <TableHeader>
            <TableRow>
              <TableHead>Partner account</TableHead>
              <TableHead>Tier</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Default promotion</TableHead>
              <TableHead>Automatic formats</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!loading &&
              result.items.map((account) => (
                <TableRow key={account.id}>
                  <TableCell>
                    <Link
                      href={`${accountSettingsPath(account.id)}${query}`}
                      prefetch={false}
                      className="font-medium text-primary hover:underline"
                    >
                      {account.name}
                    </Link>
                  </TableCell>
                  <TableCell className="capitalize">{account.tier}</TableCell>
                  <TableCell>
                    <Badge variant={account.status === "active" ? "success" : "warning"}>
                      {account.status}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {account.effective_promotion_kind === "ad"
                      ? "Sponsored"
                      : "Product recommendation"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {account.ad_formats?.map(adFormatLabel).join(", ") || "None"}
                  </TableCell>
                  <TableCell>
                    <Button variant="outline" size="icon-sm" asChild>
                      <Link
                        href={`${accountSettingsPath(account.id)}${query}`}
                        prefetch={false}
                        aria-label={`Edit settings for ${account.name}`}
                      >
                        <Pencil aria-hidden />
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            {loading && (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                  Loading partner accounts…
                </TableCell>
              </TableRow>
            )}
            {!loading && !result.error && !result.items.length && (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                  {page.q
                    ? "No partner accounts match your search."
                    : "No partner accounts are connected to your profile yet."}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <nav
        aria-label="Account pages"
        className="flex flex-wrap items-center justify-between gap-3 text-sm"
      >
        <span>
          {result.total
            ? `${page.offset + 1}–${Math.min(page.offset + 25, result.total)} of ${result.total}`
            : "0 partner accounts"}
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            aria-label="Previous accounts"
            disabled={loading || page.offset === 0}
            onClick={() =>
              setPage((current) => ({ ...current, offset: Math.max(0, current.offset - 25) }))
            }
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="outline"
            aria-label="Next accounts"
            disabled={loading || page.offset + 25 >= result.total}
            onClick={() => setPage((current) => ({ ...current, offset: current.offset + 25 }))}
          >
            Next
          </Button>
        </div>
      </nav>
    </section>
  );
}
