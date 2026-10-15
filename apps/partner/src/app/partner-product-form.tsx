"use client";

import { useId, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@devfeed/ui/button";
import { Input } from "@devfeed/ui/input";
import { Select } from "@devfeed/ui/select";
import { ResourceHeading, ResourcePanel } from "@devfeed/ui/resource-layout";
import {
  PartnerProductCreateResponse,
  PartnerProductUpdateResponse,
} from "@/lib/api/generated/validators";
import type { ProductDetailOut, ManualProductInput } from "@/lib/api/generated/models";
import type { Account } from "@/lib/types";
import { portalPath, productPath } from "@/lib/routes";
import { isPartnerRequestCancelled, usePartnerRequest } from "@/lib/partner-request";
import { PartnerAccountPicker } from "./partner-account-picker";

const conflictMessage =
  "This product changed or its website already belongs to another catalog product. Reload the page and check the website before saving again.";

export function PartnerProductCreate({
  account,
  csrfToken,
  onUnavailable,
  query = "",
}: {
  account?: Account;
  csrfToken: string;
  onUnavailable: () => void;
  query?: string;
}) {
  return (
    <section className="space-y-4">
      <ResourceHeading
        homeHref={portalPath()}
        homeLabel="Dashboard"
        siteTitle="DevFeed Partners"
        title="Add product"
        description="Advertise your product on DevFeed. No launch platform or import connection is required."
        trail={[{ label: "Products", href: `${portalPath("products")}${query}` }]}
      />
      <PartnerProductForm
        account={account}
        csrfToken={csrfToken}
        onUnavailable={onUnavailable}
        cancelHref={`${portalPath("products")}${query}`}
        query={query}
      />
    </section>
  );
}

export function PartnerProductForm({
  account: initialAccount,
  product,
  csrfToken,
  onUnavailable,
  cancelHref,
  query = "",
}: {
  account?: Account;
  product?: ProductDetailOut;
  csrfToken: string;
  onUnavailable: () => void;
  cancelHref: string;
  query?: string;
}) {
  const router = useRouter();
  const id = useId();
  const [account, setAccount] = useState(initialAccount);
  const [name, setName] = useState(product?.name ?? "");
  const [website, setWebsite] = useState(product?.product_url ?? "");
  const [tagline, setTagline] = useState(product?.tagline ?? "");
  const [pricing, setPricing] = useState<ManualProductInput["pricing"]>(
    product?.pricing ?? "unknown",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const accountId = product?.account_id ?? account?.id;
  const request = usePartnerRequest({ accountId, onUnavailable });

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    if (!accountId) {
      setError("Choose the partner account that owns this product.");
      return;
    }
    let parsed: URL;
    try {
      parsed = new URL(website.trim());
    } catch {
      setError("Enter a valid public HTTPS website URL.");
      return;
    }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
      setError("Enter a valid public HTTPS website URL.");
      return;
    }
    const normalizedTagline = tagline.trim().replace(/\s+/g, " ");
    if (!name.trim() || normalizedTagline.length < 3 || normalizedTagline.length > 160) {
      setError("Enter a product name and a tagline between 3 and 160 characters.");
      return;
    }
    setSaving(true);
    setError("");
    const facts = {
      name: name.trim(),
      product_url: website.trim(),
      tagline: normalizedTagline,
      pricing,
    };
    try {
      const result = await request(
        product ? `products/${encodeURIComponent(product.id)}` : "products",
        {
          method: product ? "PATCH" : "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
          body: JSON.stringify({
            ...facts,
            ...(product ? { expected_revision: product.revision } : {}),
          }),
          parse: (value) => {
            const result = (
              product ? PartnerProductUpdateResponse : PartnerProductCreateResponse
            ).parse(value);
            if (result.account_id !== accountId || (product && result.id !== product.id))
              throw new Error("Unexpected product.");
            if (
              product &&
              (result.name !== facts.name ||
                result.tagline !== facts.tagline ||
                result.pricing !== facts.pricing)
            )
              throw new Error("Could not confirm saved product details.");
            return result;
          },
          unavailableOnNotFound: false,
          errorMessage: product
            ? "Could not save product details. Your changes are still here."
            : "Could not add this product. Your details are still here.",
          statusMessages: {
            409: product
              ? conflictMessage
              : "This product already exists. Open the existing product to manage it.",
            422: "Check the product details and use a public HTTPS website URL.",
            429: "Too many product updates. Wait a minute and try again.",
          },
        },
      );
      router.replace(productPath(result.id, { accountId, query }));
    } catch (reason) {
      if (!isPartnerRequestCancelled(reason))
        setError(reason instanceof Error ? reason.message : "Could not save product.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <form
        aria-label={product ? "Product details" : "New product"}
        aria-busy={saving}
        onSubmit={(event) => void save(event)}
        className="min-w-0 space-y-4 rounded-lg border bg-card p-4"
      >
        {error && (
          <p role="alert" className="notice">
            {error}
          </p>
        )}
        <fieldset disabled={saving} className="min-w-0 space-y-4">
          {!product && (
            <div className="space-y-2">
              <p className="text-sm font-medium">Partner account</p>
              <PartnerAccountPicker
                account={account}
                onChange={setAccount}
                required
                disabled={saving}
                onUnavailable={onUnavailable}
              />
              <p className="text-xs text-muted-foreground">
                The product and its reporting belong to this partner account.
              </p>
            </div>
          )}
          <div className="grid items-start gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor={`${id}-name`}>
                Product name
              </label>
              <Input
                id={`${id}-name`}
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={200}
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor={`${id}-website`}>
                Website URL
              </label>
              <Input
                id={`${id}-website`}
                type="url"
                value={website}
                onChange={(event) => setWebsite(event.target.value)}
                required
                maxLength={2048}
                placeholder="https://your-product.com"
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium" htmlFor={`${id}-tagline`}>
                Tagline
              </label>
              <Input
                id={`${id}-tagline`}
                value={tagline}
                onChange={(event) => setTagline(event.target.value)}
                required
                minLength={3}
                maxLength={160}
                aria-describedby={`${id}-tagline-hint`}
              />
              <p id={`${id}-tagline-hint`} className="text-xs text-muted-foreground">
                Briefly explain what your product does in 3–160 characters. This line appears in
                product placements.
              </p>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor={`${id}-pricing`}>
                Pricing
              </label>
              <Select
                id={`${id}-pricing`}
                label="Pricing"
                value={pricing ?? "unknown"}
                onChange={(value) => {
                  if (["free", "freemium", "paid", "unknown"].includes(value))
                    setPricing(value as ManualProductInput["pricing"]);
                }}
                options={[
                  { value: "unknown", label: "Not specified" },
                  { value: "free", label: "Free" },
                  { value: "freemium", label: "Freemium" },
                  { value: "paid", label: "Paid" },
                ]}
              />
            </div>
          </div>
        </fieldset>
        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button variant="outline" asChild disabled={saving}>
            <Link href={cancelHref} prefetch={false}>
              Cancel
            </Link>
          </Button>
          <Button type="submit" loading={saving}>
            {product ? "Save product" : "Add product"}
          </Button>
        </div>
      </form>
      <ResourcePanel title="What happens next">
        <div className="space-y-3 text-sm text-muted-foreground">
          <p>
            DevFeed reviews the product website and prepares relevant contextual matches in the
            background. Adding a product does not immediately start ad delivery.
          </p>
          <p>
            Once added, choose placement formats and customize the ad image using a URL or upload.
            Account approval, audience settings, and available capacity also apply.
          </p>
          {product && (
            <p>Changing product details may require a new review before it can appear again.</p>
          )}
        </div>
      </ResourcePanel>
    </div>
  );
}
