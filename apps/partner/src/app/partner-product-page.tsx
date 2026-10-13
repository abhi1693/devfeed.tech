"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pencil } from "lucide-react";
import { ResourceHeading, ResourcePanel } from "@devfeed/ui/resource-layout";
import { ProductPlacementPreview, adFormatLabel } from "@devfeed/ui/advertising-formats";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@devfeed/ui/table";
import { parseCreativePreview } from "@/lib/contracts";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import { accountSettingsPath, portalPath, productPath } from "@/lib/routes";
import type { Account } from "@/lib/types";
import type { InventoryProductOut, WorkspaceInventoryProductOut } from "@/lib/api/generated/models";
import {
  parseProductInventory,
  parseWorkspaceProductInventory,
  productPlacementStatus,
} from "./partner-product-catalog";
import { PartnerProductPlacements } from "./partner-product-placements";

export function PartnerProductPage(props: {
  account?: Account;
  productId: string;
  edit?: boolean;
  csrfToken: string;
  query?: string;
  contextOffset?: number;
  onUnavailable: () => void;
}) {
  return props.account ? (
    <ProductDetails {...props} account={props.account} />
  ) : (
    <ProductAccounts
      productId={props.productId}
      edit={props.edit}
      query={props.query}
      initialOffset={props.contextOffset}
      onUnavailable={props.onUnavailable}
    />
  );
}

function ProductAccounts({
  productId,
  edit = false,
  query = "",
  initialOffset = 0,
  onUnavailable,
}: {
  productId: string;
  edit?: boolean;
  query?: string;
  initialOffset?: number;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const request = usePartnerRequest({ onUnavailable });
  const [offset, setOffset] = useState(initialOffset);
  const [attempt, setAttempt] = useState(0);
  const key = `${productId}/${offset}/${attempt}`;
  const [result, setResult] = useState<{
    key: string;
    items: WorkspaceInventoryProductOut[];
    total: number;
    error: string;
  }>({ key: "", items: [], total: 0, error: "" });
  const loading = result.key !== key;
  const scope = new URLSearchParams(query);
  if (offset) scope.set("policy_offset", String(offset));
  else scope.delete("policy_offset");
  const contextQuery = scope.size ? `?${scope}` : "";
  useEffect(() => {
    window.history.replaceState(
      window.history.state,
      "",
      productPath(productId, { edit, query: contextQuery }),
    );
  }, [productId, edit, contextQuery]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      product_id: productId,
      limit: "25",
      offset: String(offset),
    });
    void request(`inventory?${params}`, {
      signal: controller.signal,
      parse: (value) => {
        const result = parseWorkspaceProductInventory(value);
        if (
          result.items.some((item) => item.id !== productId) ||
          result.offset !== offset ||
          result.limit !== 25
        )
          throw new Error("Could not confirm product placement policies.");
        return result;
      },
      errorMessage: "Could not load product placement policies. Please retry.",
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= value.total) {
          setOffset(0);
          return;
        }
        if (value.total === 1 && value.items[0]) {
          router.replace(
            productPath(productId, { edit, accountId: value.items[0].account_id, query }),
          );
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
                : "Could not load product placement policies. Please retry.",
          });
      });
    return () => controller.abort();
  }, [request, productId, offset, key, edit, query, router]);
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={
          loading
            ? "Product"
            : edit
              ? "Choose a placement policy"
              : (result.items[0]?.name ?? "Product unavailable")
        }
        description="Each partner account has its own placement settings for this product."
        trail={[{ label: "Products", href: `${portalPath("products")}${query}` }]}
      />
      {result.error && (
        <div role="alert" className="notice">
          <p>{result.error}</p>
          <Button variant="outline" onClick={() => setAttempt((value) => value + 1)}>
            Retry product policies
          </Button>
        </div>
      )}
      {loading ? (
        <p role="status">Loading product placement policies…</p>
      ) : result.items.length ? (
        <>
          <div className="overflow-x-auto rounded-md border">
            <Table aria-label="Product account policies">
              <TableHeader>
                <TableRow>
                  <TableHead>Partner account</TableHead>
                  <TableHead>Placement status</TableHead>
                  <TableHead>Formats</TableHead>
                  <TableHead>Locations</TableHead>
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.items.map((item) => (
                  <TableRow key={item.account_id}>
                    <TableCell>
                      <Link
                        href={productPath(productId, {
                          edit,
                          accountId: item.account_id,
                          query: contextQuery,
                        })}
                        prefetch={false}
                        className="font-medium text-primary hover:underline"
                      >
                        {item.account_name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          productPlacementStatus(item) === "Allowed" ? "success" : "secondary"
                        }
                      >
                        {productPlacementStatus(item)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {item.ad_formats == null
                        ? "Account defaults"
                        : item.ad_formats.map(adFormatLabel).join(", ") || "None"}
                    </TableCell>
                    <TableCell>
                      {item.promotion_surfaces == null
                        ? "Account defaults"
                        : item.promotion_surfaces.join(", ") || "None"}
                    </TableCell>
                    <TableCell>
                      <Button variant="outline" size="icon-sm" asChild>
                        <Link
                          href={productPath(productId, {
                            edit: true,
                            accountId: item.account_id,
                            query: contextQuery,
                          })}
                          prefetch={false}
                          aria-label={`Edit placements for ${item.account_name}`}
                        >
                          <Pencil aria-hidden />
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <nav
            aria-label="Product policy pages"
            className="flex flex-wrap items-center justify-between gap-3 text-sm"
          >
            <span>
              {offset + 1}–{Math.min(offset + 25, result.total)} of {result.total} partner accounts
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 25))}
              >
                Previous policies
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + 25 >= result.total}
                onClick={() => setOffset(offset + 25)}
              >
                Next policies
              </Button>
            </div>
          </nav>
        </>
      ) : (
        !result.error && (
          <ResourcePanel title="Product not found">
            <p className="text-sm text-muted-foreground">
              This product is not connected to any of your partner accounts.
            </p>
          </ResourcePanel>
        )
      )}
    </section>
  );
}

function ProductDetails({
  account,
  productId,
  edit = false,
  csrfToken,
  query = "",
  onUnavailable,
}: {
  account: Account;
  productId: string;
  edit?: boolean;
  csrfToken: string;
  query?: string;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const request = usePartnerRequest({ accountId: account.id, onUnavailable });
  const [attempt, setAttempt] = useState(0);
  const key = `${account.id}/${productId}/${attempt}`;
  const [result, setResult] = useState<{
    key: string;
    product: InventoryProductOut | null;
    error: string;
  }>({ key: "", product: null, error: "" });
  const loading = result.key !== key;
  useEffect(() => {
    const controller = new AbortController();
    void request(`inventory?product_id=${encodeURIComponent(productId)}&limit=1`, {
      signal: controller.signal,
      parse: parseProductInventory,
      unavailableOnNotFound: false,
      errorMessage: "Could not load this product. Please retry.",
    })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.items.length > 1 || data.items.some((product) => product.id !== productId))
          throw new Error("Could not confirm the requested product.");
        setResult({ key, product: data.items[0] ?? null, error: "" });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setResult({
            key,
            product: null,
            error:
              reason instanceof Error
                ? reason.message
                : "Could not load this product. Please retry.",
          });
      });
    return () => controller.abort();
  }, [request, productId, key]);
  const loadPreview = useCallback(
    (signal: AbortSignal) =>
      request(`creative-preview?product_id=${encodeURIComponent(productId)}`, {
        signal,
        errorMessage: "Could not load the creative preview.",
        parse: (value) => {
          const product = parseCreativePreview(value);
          if (product && product.id !== productId) throw new Error("Unexpected preview product.");
          return product;
        },
      }),
    [request, productId],
  );
  const listHref = `${portalPath("products")}${query}`;
  const detailHref = productPath(productId, { accountId: account.id, query });
  const product = !loading ? result.product : null;
  if (!product)
    return (
      <div className="space-y-4">
        <ResourceHeading
          homeHref={portalPath()}
          homeLabel="Dashboard"
          siteTitle="DevFeed Partners"
          title={loading ? "Product" : "Product unavailable"}
          trail={[{ label: "Products", href: listHref }]}
        />
        {loading ? (
          <p role="status">Loading product…</p>
        ) : result.error ? (
          <div role="alert" className="notice">
            <p>{result.error}</p>
            <Button variant="outline" onClick={() => setAttempt((value) => value + 1)}>
              Retry product
            </Button>
          </div>
        ) : (
          <ResourcePanel title="Product not found">
            <p className="text-sm text-muted-foreground">
              This product is not connected to this partner account.
            </p>
            <Button className="mt-4" variant="outline" asChild>
              <Link href={listHref} prefetch={false}>
                Back to products
              </Link>
            </Button>
          </ResourcePanel>
        )}
      </div>
    );
  const status = productPlacementStatus(product);
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={edit ? `Edit ${product.name} placements` : product.name}
        description={
          edit
            ? "Inherit account defaults or limit where this product can appear. Excluding a product preserves its restrictions."
            : "Review this product’s availability, placement policy, and native creative."
        }
        trail={[
          { label: "Products", href: listHref },
          ...(edit ? [{ label: product.name, href: detailHref }] : []),
        ]}
      >
        {!edit && (
          <Button asChild>
            <Link
              href={productPath(productId, { edit: true, accountId: account.id, query })}
              prefetch={false}
            >
              <Pencil aria-hidden />
              Edit placements
            </Link>
          </Button>
        )}
      </ResourceHeading>
      <p className="text-sm text-muted-foreground">
        Partner account:{" "}
        <Link
          href={accountSettingsPath(account.id)}
          prefetch={false}
          className="text-primary hover:underline"
        >
          {account.name}
        </Link>
        .{" "}
        <Link
          href={productPath(productId, { edit, query })}
          prefetch={false}
          className="text-primary hover:underline"
        >
          View all account policies
        </Link>
      </p>
      {edit ? (
        <ResourcePanel title="Product placement settings">
          <PartnerProductPlacements
            accountId={account.id}
            productId={productId}
            name={product.name}
            allowedFormats={account.allowed_ad_formats ?? ["feed"]}
            accountFormats={account.ad_formats ?? ["feed"]}
            accountSurfaces={account.promotion_surfaces}
            destination={account.outbound_destination ?? "product_url"}
            csrfToken={csrfToken}
            onUnavailable={onUnavailable}
            onCancel={() => router.replace(detailHref)}
            onSaved={() => router.replace(detailHref)}
          />
        </ResourcePanel>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <div className="space-y-4">
            <ResourcePanel
              title="Product"
              fields={[
                {
                  label: "Placement status",
                  value: (
                    <Badge variant={status === "Allowed" ? "success" : "secondary"}>{status}</Badge>
                  ),
                },
                { label: "Product review", value: product.status.replaceAll("_", " ") },
                {
                  label: "Account acceptance",
                  value: product.commercial_approved ? "Accepted" : "Awaiting acceptance",
                },
                { label: "Technologies", value: product.technologies.join(", ") || "—" },
              ]}
            />
            <ResourcePanel
              title="Placement policy"
              fields={[
                {
                  label: "Availability",
                  value: product.excluded
                    ? "Excluded from this account’s placements"
                    : "Allowed by product policy",
                },
                {
                  label: "Formats",
                  value:
                    product.ad_formats == null
                      ? "Inherit account defaults"
                      : product.ad_formats.map(adFormatLabel).join(", ") || "None",
                },
                {
                  label: "Locations",
                  value:
                    product.promotion_surfaces == null
                      ? "Inherit account defaults"
                      : product.promotion_surfaces.join(", ") || "None",
                },
              ]}
            >
              <p className="mt-3 text-sm text-muted-foreground">
                Delivery also depends on approval, relevance, audience, account status, and
                available capacity.{" "}
                <Link
                  href={accountSettingsPath(account.id)}
                  prefetch={false}
                  className="text-primary hover:underline"
                >
                  View account settings
                </Link>
                .
              </p>
            </ResourcePanel>
          </div>
          <ResourcePanel title="Creative preview">
            <ProductPlacementPreview
              allowedFormats={account.allowed_ad_formats ?? ["feed"]}
              initialFormat={account.ad_formats?.[0] ?? "feed"}
              destination={account.outbound_destination ?? "product_url"}
              load={loadPreview}
            />
          </ResourcePanel>
        </div>
      )}
    </section>
  );
}
