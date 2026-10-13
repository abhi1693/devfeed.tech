"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import {
  parseProductPlacementPolicy,
  type ProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";
import { PartnerProductPlacements } from "./partner-product-placements";
import { Button } from "@/components/atoms/button";
import type { Account } from "@/lib/types";

type Product = ProductPlacementPolicy & { id: string; name: string; globally_excluded?: boolean };
export function PartnerProductCatalog({
  account,
  csrfToken,
  onUnavailable,
}: {
  account: Account;
  csrfToken: string;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const access = useRef({ router, onUnavailable });
  useEffect(() => {
    access.current = { router, onUnavailable };
  }, [router, onUnavailable]);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState({ q: "", offset: 0 });
  const [revision, setRevision] = useState(0);
  const key = `${account.id}/${page.q}/${page.offset}/${revision}`;
  const [result, setResult] = useState<{
    key: string;
    items: Product[];
    total: number;
    error: string;
  }>({ key: "", items: [], total: 0, error: "" });
  const loading = result.key !== key || search.trim() !== page.q;
  useEffect(() => {
    if (!open || search.trim() === page.q) return;
    const timer = setTimeout(() => setPage({ q: search.trim(), offset: 0 }), 250);
    return () => clearTimeout(timer);
  }, [open, search, page.q]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ q: page.q, offset: String(page.offset), limit: "25" });
    void (async () => {
      try {
        const response = await fetch(`/api/v1/partner/accounts/${account.id}/inventory?${query}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (response.status === 401) {
          access.current.router.replace("/login");
          access.current.onUnavailable();
        } else if (response.status === 404) access.current.onUnavailable();
        if (!response.ok) throw new Error("Could not load imported products. Please retry.");
        const data = await response.json();
        if (!Array.isArray(data.items) || typeof data.total !== "number")
          throw new Error("Could not load imported products. Please retry.");
        const items = data.items.map((item: Product) => {
          if (!item || typeof item.id !== "string" || typeof item.name !== "string")
            throw new Error("Invalid imported product.");
          return { ...item, ...parseProductPlacementPolicy(item) };
        });
        if (!controller.signal.aborted) setResult({ key, items, total: data.total, error: "" });
      } catch {
        if (!controller.signal.aborted)
          setResult({
            key,
            items: [],
            total: 0,
            error: "Could not load imported products. Please retry.",
          });
      }
    })();
    return () => controller.abort();
  }, [open, account.id, page, key]);
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSearch("");
          setPage({ q: "", offset: 0 });
          setRevision((value) => value + 1);
        }
      }}
    >
      <Dialog.Trigger asChild>
        <Button type="button" variant="outline">
          Manage product placements
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%-2rem)] max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-5 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">Imported product placements</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">
            Configure imported products before they receive impressions, including excluded
            products.
          </Dialog.Description>
          <label className="mt-5 block text-sm font-medium" htmlFor="placement-product-search">
            Search imported products
          </label>
          <input
            id="placement-product-search"
            type="search"
            value={search}
            maxLength={200}
            placeholder="Search imported products…"
            className="mt-2 w-full rounded-md border bg-background px-3 py-2 text-sm"
            onChange={(event) => setSearch(event.target.value)}
          />
          <div aria-busy={loading} className="mt-4">
            {loading ? (
              <p role="status" className="py-5 text-sm">
                Loading imported products…
              </p>
            ) : result.error ? (
              <div role="alert" className="space-y-3 py-4 text-sm">
                <p>{result.error}</p>
                <button
                  type="button"
                  className="underline"
                  onClick={() => setRevision((value) => value + 1)}
                >
                  Retry imported products
                </button>
              </div>
            ) : result.items.length ? (
              <ul className="divide-y rounded-lg border">
                {result.items.map((product) => (
                  <li
                    key={product.id}
                    className="flex flex-wrap items-center justify-between gap-3 p-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-sm font-medium">{product.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {product.globally_excluded
                          ? "Blocked by product checks"
                          : product.excluded
                            ? "Excluded"
                            : product.ad_formats !== null || product.promotion_surfaces !== null
                              ? "Custom restrictions"
                              : "Inherits account defaults"}
                      </p>
                    </div>
                    <PartnerProductPlacements
                      accountId={account.id}
                      productId={product.id}
                      name={product.name}
                      allowedFormats={account.allowed_ad_formats ?? ["feed"]}
                      accountFormats={account.ad_formats ?? ["feed"]}
                      accountSurfaces={account.promotion_surfaces}
                      destination={account.outbound_destination ?? "product_url"}
                      csrfToken={csrfToken}
                      onSaved={() => setRevision((value) => value + 1)}
                      onUnavailable={onUnavailable}
                    />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="py-5 text-sm text-muted-foreground">No matching imported products.</p>
            )}
          </div>
          <nav
            aria-label="Imported product pages"
            className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"
          >
            <span>
              {result.total
                ? `${page.offset + 1}–${Math.min(page.offset + 25, result.total)} of ${result.total}`
                : "0 products"}
            </span>
            <div className="flex gap-2">
              <Button
                type="button"
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
                type="button"
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
          <div className="mt-5 flex justify-end">
            <Dialog.Close asChild>
              <Button type="button" variant="outline">
                Close product catalog
              </Button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
