"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Pencil, RefreshCw, Search } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { Input } from "@devfeed/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@devfeed/ui/table";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import {
  PartnerInventoryListResponse,
  PartnerWorkspaceInventoryResponse,
} from "@/lib/api/generated/validators";
import { parseProductPlacementPolicy } from "@devfeed/ui/advertising-formats";
import { accountSettingsPath, portalPath, productPath } from "@/lib/routes";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import type {
  InventoryOut,
  InventoryProductOut,
  WorkspaceInventoryOut,
  WorkspaceInventoryProductOut,
} from "@/lib/api/generated/models";
import type { Account } from "@/lib/types";

export function parseProductInventory(value: unknown): InventoryOut {
  const data = PartnerInventoryListResponse.parse(value);
  if (data.total < 0) throw new Error("Invalid imported product count.");
  return {
    ...data,
    items: data.items.map((item) => ({ ...item, ...parseProductPlacementPolicy(item) })),
  };
}

export function parseWorkspaceProductInventory(value: unknown): WorkspaceInventoryOut {
  const data = PartnerWorkspaceInventoryResponse.parse(value);
  if (data.total < 0 || data.offset < 0 || data.limit < 1)
    throw new Error("Invalid imported product page.");
  return {
    ...data,
    items: data.items.map((item) => ({ ...item, ...parseProductPlacementPolicy(item) })),
  };
}

export function productPlacementStatus(product: InventoryProductOut) {
  if (product.globally_excluded) return "Blocked";
  if (product.excluded) return "Excluded";
  if (!product.commercial_approved) return "In review";
  if (product.ad_formats?.length === 0 || product.promotion_surfaces?.length === 0)
    return "No placements";
  return "Allowed";
}

export function PartnerProductCatalog({
  accountFilter,
  onUnavailable,
  initialSearch = "",
  initialOffset = 0,
  query = "",
}: {
  accountFilter?: Account;
  onUnavailable: () => void;
  initialSearch?: string;
  initialOffset?: number;
  query?: string;
}) {
  const request = usePartnerRequest({ onUnavailable });
  const [search, setSearch] = useState(initialSearch);
  const [page, setPage] = useState({ q: initialSearch, offset: initialOffset });
  const [revision, setRevision] = useState(0);
  const key = `${accountFilter?.id ?? "all"}/${page.q}/${page.offset}/${revision}`;
  const [result, setResult] = useState<{
    key: string;
    items: WorkspaceInventoryProductOut[];
    total: number;
    error: string;
  }>({ key: "", items: [], total: 0, error: "" });
  const loading = result.key !== key || search.trim() !== page.q;
  const scope = new URLSearchParams(query);
  if (page.q) scope.set("q", page.q);
  else scope.delete("q");
  if (page.offset) scope.set("product_offset", String(page.offset));
  else scope.delete("product_offset");
  if (accountFilter) scope.set("account_id", accountFilter.id);
  else scope.delete("account_id");
  const listQuery = scope.size ? `?${scope}` : "";
  useEffect(() => {
    if (search.trim() === page.q) return;
    const timer = setTimeout(() => setPage({ q: search.trim(), offset: 0 }), 250);
    return () => clearTimeout(timer);
  }, [search, page.q]);
  useEffect(() => {
    window.history.replaceState(window.history.state, "", `${portalPath("products")}${listQuery}`);
  }, [listQuery]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ q: page.q, offset: String(page.offset), limit: "25" });
    if (accountFilter) params.set("account_id", accountFilter.id);
    void request(`inventory?${params}`, {
      signal: controller.signal,
      parse: (value) => {
        const data = parseWorkspaceProductInventory(value);
        if (
          data.offset !== page.offset ||
          data.limit !== 25 ||
          (accountFilter && data.items.some((product) => product.account_id !== accountFilter.id))
        )
          throw new Error("Could not confirm the imported product page.");
        return data;
      },
      errorMessage: "Could not load imported products. Please retry.",
    })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (page.offset > 0 && page.offset >= data.total) {
          setPage((current) => ({ ...current, offset: 0 }));
          return;
        }
        setResult({ key, ...data, error: "" });
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
                : "Could not load imported products. Please retry.",
          });
      });
    return () => controller.abort();
  }, [request, page, key, accountFilter]);
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title="Products"
        description="Your imported catalog across all partner accounts. Open a product to review its placement settings."
      >
        <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
          <RefreshCw aria-hidden />
          Refresh
        </Button>
        <Button asChild>
          <Link href={`${portalPath("connections")}${query}`} prefetch={false}>
            Manage connections
          </Link>
        </Button>
      </ResourceHeading>
      {accountFilter && (
        <p className="text-sm text-muted-foreground">
          Filtered to {accountFilter.name}.{" "}
          <Link href={`${portalPath("products")}${query}`} className="text-primary hover:underline">
            Show all partner accounts
          </Link>
        </p>
      )}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid w-full gap-1.5 sm:max-w-sm">
          <label htmlFor="placement-product-search" className="text-sm font-medium">
            Search products
          </label>
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground"
            />
            <Input
              id="placement-product-search"
              type="search"
              className="pl-9"
              value={search}
              maxLength={200}
              placeholder="Search products…"
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>
        <p className="text-sm text-muted-foreground" role="status">
          {loading
            ? "Loading imported products…"
            : `${result.total.toLocaleString("en-US")} ${result.total === 1 ? "product" : "products"}`}
        </p>
      </div>
      {result.error && (
        <div role="alert" className="notice">
          <p>{result.error}</p>
          <Button variant="outline" onClick={() => setRevision((value) => value + 1)}>
            Retry imported products
          </Button>
        </div>
      )}
      <div className="overflow-x-auto rounded-md border" aria-busy={loading}>
        <Table aria-label="Imported products" className="[&_td]:py-1.5">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Partner account</TableHead>
              <TableHead>Placement status</TableHead>
              <TableHead>Technologies</TableHead>
              <TableHead>Settings</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!loading &&
              result.items.map((product) => {
                const status = productPlacementStatus(product);
                return (
                  <TableRow key={`${product.account_id}/${product.id}`}>
                    <TableCell className="font-medium">
                      <Link
                        href={productPath(product.id, {
                          accountId: product.account_id,
                          query: listQuery,
                        })}
                        prefetch={false}
                        className="text-primary hover:underline"
                      >
                        {product.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={accountSettingsPath(product.account_id)}
                        prefetch={false}
                        className="text-primary hover:underline"
                      >
                        {product.account_name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          status === "Allowed"
                            ? "success"
                            : status === "Excluded" || status === "Blocked"
                              ? "secondary"
                              : "warning"
                        }
                      >
                        {status}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className="max-w-64 truncate text-muted-foreground"
                      title={product.technologies.join(", ")}
                    >
                      {product.technologies.slice(0, 3).join(", ") || "—"}
                      {product.technologies.length > 3 && ` +${product.technologies.length - 3}`}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {(product.ad_formats !== null && product.ad_formats !== undefined) ||
                      (product.promotion_surfaces !== null &&
                        product.promotion_surfaces !== undefined)
                        ? "Custom"
                        : "Account defaults"}
                    </TableCell>
                    <TableCell>
                      <Button size="icon-sm" variant="outline" asChild>
                        <Link
                          href={productPath(product.id, {
                            edit: true,
                            accountId: product.account_id,
                            query: listQuery,
                          })}
                          prefetch={false}
                          aria-label={`Edit placements for ${product.name}`}
                        >
                          <Pencil aria-hidden />
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            {!loading && !result.error && !result.items.length && (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                  {page.q
                    ? "No products match your search."
                    : "No imported products yet. Connect a catalog to get started."}
                </TableCell>
              </TableRow>
            )}
            {loading && (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                  Loading imported products…
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <nav
        aria-label="Imported product pages"
        className="flex flex-wrap items-center justify-between gap-3 text-sm"
      >
        <span>
          {result.total
            ? `${page.offset + 1}–${Math.min(page.offset + 25, result.total)} of ${result.total}`
            : "0 products"}
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            aria-label="Previous imported products"
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
            aria-label="Next imported products"
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
