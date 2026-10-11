"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Select } from "@devfeed/ui/select";
import {
  ProductDestinationPreview,
  type DestinationPreviewProduct,
} from "@devfeed/ui/product-destination-preview";
import { Button } from "@/components/atoms/button";
import type { Account, OutboundDestination } from "@/lib/types";

type Props = {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
};
function parsePreview(value: unknown): DestinationPreviewProduct | null {
  if (!value || typeof value !== "object" || !("product" in value))
    throw new Error("Could not load the product preview. Please retry.");
  const product = value.product;
  if (product === null) return null;
  const row = product as Record<string, unknown>;
  if (
    !product ||
    typeof product !== "object" ||
    !["id", "name", "product_url"].every((key) => typeof row[key] === "string") ||
    !(row.listing_url === null || typeof row.listing_url === "string")
  )
    throw new Error("Could not load the product preview. Please retry.");
  return product as DestinationPreviewProduct;
}

export function PartnerPreferences(props: Props) {
  return <AccountPreferences key={props.account.id} {...props} />;
}

function AccountPreferences({ account, csrfToken, onSaved, onUnavailable }: Props) {
  const router = useRouter();
  const [destination, setDestination] = useState<OutboundDestination>(
    account.outbound_destination ?? "product_url",
  );
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{
    product: DestinationPreviewProduct | null;
    loading: boolean;
    error: string;
  }>({ product: null, loading: true, error: "" });
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const request = useRef<AbortController | null>(null);
  const access = useRef({ router, onUnavailable });
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    access.current = { router, onUnavailable };
  }, [router, onUnavailable]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/v1/partner/accounts/${account.id}/destination-preview`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if ([401, 403, 404].includes(response.status)) {
          if (response.status === 401 || response.status === 403)
            access.current.router.replace("/login");
          access.current.onUnavailable();
          return;
        }
        if (!response.ok) throw new Error("Could not load the product preview. Please retry.");
        const product = parsePreview(await response.json());
        if (!controller.signal.aborted) setPreview({ product, loading: false, error: "" });
      } catch (reason) {
        if (!controller.signal.aborted)
          setPreview({
            product: null,
            loading: false,
            error:
              reason instanceof Error
                ? reason.message
                : "Could not load the product preview. Please retry.",
          });
      }
    })();
    return () => controller.abort();
  }, [account.id, previewAttempt]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch(`/api/v1/partner/accounts/${account.id}/preferences`, {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
        body: JSON.stringify({ outbound_destination: destination }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (response.status === 401) {
        router.replace("/login");
        onUnavailable();
        return;
      }
      if (response.status === 404) {
        onUnavailable();
        return;
      }
      if (!response.ok)
        throw new Error(
          response.status === 403
            ? "Could not update this account. Refresh the page and try again."
            : "Could not save the click destination. Please retry.",
        );
      const saved = (await response.json()) as Account;
      if (
        saved.id !== account.id ||
        !["product_url", "listing_url"].includes(saved.outbound_destination ?? "")
      )
        throw new Error("Could not confirm the saved destination. Refresh and try again.");
      if (!controller.signal.aborted) {
        onSaved(saved);
        setMessage("Click destination saved.");
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not save. Please retry.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  return (
    <section className="card mt-6" aria-label="Product promotion settings">
      <h2>Product promotion settings</h2>
      <p className="muted mb-5">
        Imported products inherit your account’s promotion type. Contextual matches are preferred;
        sponsored feed placements can fill unmatched feeds. Article and topic placements require a
        relevant match.
      </p>
      <dl className="mb-6 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="muted">Default promotion</dt>
          <dd className="mt-1 font-medium">
            {account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"}
            {account.promotion_kind_source === "tier"
              ? " · inherited from tier"
              : " · account setting"}
          </dd>
        </div>
        <div>
          <dt className="muted">Imported product acceptance</dt>
          <dd className="mt-1 font-medium">
            {account.approval_mode === "trusted"
              ? "Trusted partner imports accepted automatically"
              : "Automatic capability checks"}
          </dd>
        </div>
      </dl>
      <div className="grid gap-6 lg:grid-cols-2">
        <form onSubmit={save} className="grid min-w-0 content-start gap-3">
          <label htmlFor="partner-click-destination" className="text-sm font-medium">
            Product click destination
          </label>
          <Select
            id="partner-click-destination"
            label="Product click destination"
            value={destination}
            required
            onChange={(value) => {
              if (value !== "product_url" && value !== "listing_url") return;
              setDestination(value);
              setMessage("");
            }}
            disabled={saving}
            aria-describedby="partner-click-destination-help"
            options={[
              { value: "product_url", label: "Product website" },
              { value: "listing_url", label: "Partner listing" },
            ]}
          />
          <p id="partner-click-destination-help" className="muted text-sm">
            Choose where readers go from your product cards. Both destinations include DevFeed UTM
            attribution. Products without a partner listing cannot appear when Partner listing is
            selected.
          </p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {message && (
            <p role="status" className="text-sm">
              {message}
            </p>
          )}
          <div>
            <Button type="submit" loading={saving}>
              Save click destination
            </Button>
          </div>
        </form>
        <ProductDestinationPreview
          product={preview.product}
          destination={destination}
          loading={preview.loading}
          error={preview.error}
          onRetry={() => {
            setPreview({ product: null, loading: true, error: "" });
            setPreviewAttempt((attempt) => attempt + 1);
          }}
        />
      </div>
    </section>
  );
}
