"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pencil, Archive, RotateCcw } from "lucide-react";
import { ResourceHeading, ResourcePanel } from "@devfeed/ui/resource-layout";
import { ProductPlacementPreview, adFormatLabel } from "@devfeed/ui/advertising-formats";
import { Button } from "@devfeed/ui/button";
import { Badge } from "@devfeed/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@devfeed/ui/table";
import { parseCreativePreview } from "@/lib/contracts";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import { accountSettingsPath, portalPath, productPath } from "@/lib/routes";
import type { Account } from "@/lib/types";
import type { ProductDetailOut, WorkspaceInventoryProductOut } from "@/lib/api/generated/models";
import { parseWorkspaceProductInventory, productPlacementStatus } from "./partner-product-catalog";
import { PartnerProductPlacements } from "./partner-product-placements";
import { PartnerProductForm } from "./partner-product-form";
import {
  PartnerProductGetResponse,
  PartnerProductUpdateResponse,
} from "@/lib/api/generated/validators";

export function PartnerProductPage(props: {
  account?: Account;
  productId: string;
  edit?: boolean;
  action?: "edit" | "archive";
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
      action={props.action}
      query={props.query}
      initialOffset={props.contextOffset}
      onUnavailable={props.onUnavailable}
    />
  );
}

function ProductAccounts({
  productId,
  edit = false,
  action,
  query = "",
  initialOffset = 0,
  onUnavailable,
}: {
  productId: string;
  edit?: boolean;
  action?: "edit" | "archive";
  query?: string;
  initialOffset?: number;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const request = usePartnerRequest({ onUnavailable });
  const [offset, setOffset] = useState(initialOffset);
  const [attempt, setAttempt] = useState(0);
  const includeArchived = new URLSearchParams(query).get("include_archived") === "true";
  const key = `${productId}/${offset}/${includeArchived}/${attempt}`;
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
      productPath(productId, { edit, action, query: contextQuery }),
    );
  }, [productId, edit, action, contextQuery]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      product_id: productId,
      limit: "25",
      offset: String(offset),
    });
    if (includeArchived) params.set("include_archived", "true");
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
            productPath(productId, { edit, action, accountId: value.items[0].account_id, query }),
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
  }, [request, productId, offset, key, edit, action, query, router, includeArchived]);
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={
          loading
            ? "Product"
            : action
              ? "Choose a partner account"
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
                          action,
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
  action,
  csrfToken,
  query = "",
  onUnavailable,
}: {
  account: Account;
  productId: string;
  edit?: boolean;
  action?: "edit" | "archive";
  csrfToken: string;
  query?: string;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const request = usePartnerRequest({ accountId: account.id, onUnavailable });
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState("");
  const key = `${account.id}/${productId}/${attempt}`;
  const [result, setResult] = useState<{
    key: string;
    product: ProductDetailOut | null;
    error: string;
  }>({ key: "", product: null, error: "" });
  const loading = result.key !== key;
  useEffect(() => {
    const controller = new AbortController();
    void request(`products/${encodeURIComponent(productId)}`, {
      signal: controller.signal,
      parse: (value) => {
        const product = PartnerProductGetResponse.parse(value);
        if (product.id !== productId || product.account_id !== account.id)
          throw new Error("Unexpected product.");
        return product;
      },
      unavailableOnNotFound: false,
      errorMessage: "Could not load this product. Please retry.",
    })
      .then((data) => {
        if (controller.signal.aborted) return;
        setResult({ key, product: data, error: "" });
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
  }, [request, productId, key, account.id]);
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
  const status = product.active ? productPlacementStatus(product) : "Archived";
  async function changeAvailability(archive: boolean) {
    if (!product || busy) return;
    setBusy(true);
    setWriteError("");
    try {
      await request(`products/${encodeURIComponent(productId)}`, {
        method: archive ? "DELETE" : "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
        ...(archive
          ? {}
          : { body: JSON.stringify({ active: true, expected_revision: product.revision }) }),
        parse: (value) => {
          if (archive) {
            if (value !== null) throw new Error("Unexpected archive response.");
            return null;
          }
          const saved = PartnerProductUpdateResponse.parse(value);
          if (
            saved.id !== productId ||
            saved.account_id !== account.id ||
            saved.manual_active !== true
          )
            throw new Error("Could not confirm restored product.");
          return saved;
        },
        unavailableOnNotFound: false,
        errorMessage: archive
          ? "Could not archive this product. Please retry."
          : "Could not restore this product. Please retry.",
        statusMessages: { 409: "This product changed. Reload the page before trying again." },
      });
      if (archive) router.replace(listHref);
      else setAttempt((value) => value + 1);
    } catch (reason) {
      if (!isPartnerRequestCancelled(reason))
        setWriteError(reason instanceof Error ? reason.message : "Could not update product.");
    } finally {
      setBusy(false);
    }
  }
  const canEdit = product.editable && product.source === "manual";
  const canArchive = product.manual_active === true;
  const canRestore = product.manual_active === false;
  const factsAction = action === "edit" && canEdit;
  const archiveAction = action === "archive" && product.manual_active !== null;

  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title={
          factsAction
            ? `Edit ${product.name}`
            : archiveAction
              ? `Archive ${product.name}`
              : edit
                ? `Edit ${product.name} placements`
                : product.name
        }
        description={
          factsAction
            ? "Update the product information used for review and contextual matching."
            : archiveAction
              ? "Remove the manually added product while preserving its reporting."
              : edit
                ? "Inherit account defaults or limit where this product can appear. Excluding a product preserves its restrictions."
                : undefined
        }
        trail={[
          { label: "Products", href: listHref },
          ...(edit || action ? [{ label: product.name, href: detailHref }] : []),
        ]}
      >
        {!edit && !action && product.active && (
          <>
            {canEdit && (
              <Button asChild variant="outline">
                <Link
                  href={productPath(productId, { action: "edit", accountId: account.id, query })}
                  prefetch={false}
                >
                  <Pencil aria-hidden />
                  Edit product
                </Link>
              </Button>
            )}
            {canArchive && (
              <Button asChild variant="outline">
                <Link
                  href={productPath(productId, { action: "archive", accountId: account.id, query })}
                  prefetch={false}
                >
                  <Archive aria-hidden />
                  Archive product
                </Link>
              </Button>
            )}
            <Button asChild>
              <Link
                href={productPath(productId, { edit: true, accountId: account.id, query })}
                prefetch={false}
              >
                <Pencil aria-hidden />
                Edit placements
              </Link>
            </Button>
          </>
        )}
      </ResourceHeading>
      <p className="text-sm text-muted-foreground">
        Account:{" "}
        <Link
          href={accountSettingsPath(account.id)}
          prefetch={false}
          className="text-primary hover:underline"
        >
          {account.name}
        </Link>
      </p>
      {writeError && (
        <p role="alert" className="notice">
          {writeError}
        </p>
      )}
      {canRestore && (
        <ResourcePanel
          title={product.active ? "Manually added entry archived" : "Archived product"}
        >
          <p className="text-sm text-muted-foreground">
            {product.active
              ? "The manually added entry is archived. An active import connection keeps this product available for placements."
              : "This product is no longer eligible for placements from this account. Its reporting and placement settings are preserved."}
          </p>
          <Button className="mt-3" loading={busy} onClick={() => void changeAvailability(false)}>
            <RotateCcw aria-hidden />
            Restore product
          </Button>
        </ResourcePanel>
      )}
      {action && !factsAction && !archiveAction && (
        <ResourcePanel title="Product details are managed elsewhere">
          <p className="text-sm text-muted-foreground">
            {product.source === "imported"
              ? "Update this product through its import connection. You can still customize its placement settings here."
              : "This website already belongs to a shared catalog product. Its facts are managed by DevFeed; your placement settings remain independent."}
          </p>
          <Button className="mt-3" variant="outline" asChild>
            <Link href={detailHref} prefetch={false}>
              Back to product
            </Link>
          </Button>
        </ResourcePanel>
      )}
      {factsAction ? (
        <PartnerProductForm
          account={account}
          product={product}
          csrfToken={csrfToken}
          onUnavailable={onUnavailable}
          cancelHref={detailHref}
          query={query}
        />
      ) : archiveAction ? (
        <ResourcePanel title="Archive this product?">
          <div className="space-y-3 text-sm">
            <p>
              Remove the manually added {product.name} from this account. Existing performance
              reports and placement settings will remain available.
            </p>
            <p>
              Products also supplied by an active import connection remain available for placements.
            </p>
            <p>
              You can restore it from Products by enabling Show archived products and opening the
              product.
            </p>
            <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
              <Button variant="outline" asChild disabled={busy}>
                <Link href={detailHref} prefetch={false}>
                  Cancel
                </Link>
              </Button>
              <Button
                variant="destructive"
                loading={busy}
                onClick={() => void changeAvailability(true)}
              >
                Archive product
              </Button>
            </div>
          </div>
        </ResourcePanel>
      ) : action ? null : edit && product.active ? (
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
                {
                  label: "Source",
                  value: product.source === "manual" ? "Added manually" : "Import connection",
                },
                {
                  label: "Website",
                  value: (
                    <a
                      href={product.product_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="break-all text-primary hover:underline"
                    >
                      {product.product_url}
                    </a>
                  ),
                },
                { label: "Tagline", value: product.tagline },
                {
                  label: "Pricing",
                  value: product.pricing === "unknown" ? "Not specified" : product.pricing,
                },
                {
                  label: "Product review",
                  value: {
                    waiting: "Waiting for review",
                    checking: "Checking website",
                    qualified: "Qualified",
                    attention: "Needs attention",
                    irrelevant: "Not relevant",
                    withdrawn: "Withdrawn",
                  }[product.assessment.state ?? "waiting"],
                },
                {
                  label: "Account acceptance",
                  value: product.commercial_approved ? "Accepted" : "Awaiting acceptance",
                },
                { label: "Technologies", value: product.technologies.join(", ") || "—" },
              ]}
            >
              {product.assessment.reason && (
                <details
                  className="mt-3 border-t pt-3 text-sm"
                  open={["attention", "irrelevant", "withdrawn"].includes(
                    product.assessment.state ?? "waiting",
                  )}
                >
                  <summary className="cursor-pointer font-medium">Review details</summary>
                  <p className="mt-2 text-muted-foreground">{product.assessment.reason}</p>
                </details>
              )}
              {product.source === "manual" && !product.editable && (
                <p className="mt-3 text-sm text-muted-foreground">
                  Product details are managed by DevFeed.
                </p>
              )}
            </ResourcePanel>
            <ResourcePanel
              title="Placements"
              actions={
                <Link
                  href={accountSettingsPath(account.id)}
                  prefetch={false}
                  className="text-xs text-primary hover:underline"
                >
                  Account settings
                </Link>
              }
              fields={[
                {
                  label: "Formats",
                  value:
                    product.ad_formats == null
                      ? "Account defaults"
                      : product.ad_formats.map(adFormatLabel).join(", ") || "None",
                },
                {
                  label: "Locations",
                  value:
                    product.promotion_surfaces == null
                      ? "Account defaults"
                      : product.promotion_surfaces.join(", ") || "None",
                },
              ]}
            />
          </div>
          {product.active && (
            <ResourcePanel title="Creative preview">
              <ProductPlacementPreview
                allowedFormats={account.allowed_ad_formats ?? ["feed"]}
                initialFormat={account.ad_formats?.[0] ?? "feed"}
                destination={account.outbound_destination ?? "product_url"}
                load={loadPreview}
              />
            </ResourcePanel>
          )}
        </div>
      )}
    </section>
  );
}
