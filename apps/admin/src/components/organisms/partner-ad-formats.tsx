"use client";

import { useCallback, useState } from "react";
import {
  AdCreativePreview,
  AutomaticAdFormats,
  adFormats,
  automaticAdFormats,
  type AdFormat,
  type CreativePreviewArticle,
  type CreativePreviewProduct,
  ProductPlacementDialog,
  parseProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";
import {
  adminPartnerCreativePreview,
  adminPartnerSponsorArticles,
  adminPartnerProductPromotionGet,
  adminPartnerProductPromotionUpdate,
} from "@/lib/api/generated/admin";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { useRequest } from "@/lib/use-request";
import { Select } from "@/components/molecules/select";
import { useAdmin } from "@/components/molecules/admin-session";

export function PartnerProductPlacements({
  accountId,
  product,
  allowedFormats,
  disabled,
  onSaved,
}: {
  accountId: string;
  product: { id: string; name: string };
  allowedFormats: AdFormat[];
  disabled?: boolean;
  onSaved: () => void;
}) {
  const admin = useAdmin();
  return (
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowedFormats}
      disabled={disabled}
      onSaved={onSaved}
      loadPolicy={async (signal) => {
        try {
          return parseProductPlacementPolicy(
            await adminPartnerProductPromotionGet(accountId, product.id, { signal }),
          );
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) returnToLogin();
          throw error;
        }
      }}
      savePolicy={async (restrictions, signal) => {
        try {
          return parseProductPlacementPolicy(
            await adminPartnerProductPromotionUpdate(accountId, product.id, restrictions, {
              signal,
              headers: { "X-CSRF-Token": admin.csrf_token },
            }),
          );
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) returnToLogin();
          throw error;
        }
      }}
    />
  );
}

export function PartnerCreativePreview({
  accountId,
  format,
  productId,
  articleId,
  destination = "product_url",
}: {
  accountId?: string;
  format: AdFormat;
  productId?: string;
  articleId?: string;
  destination?: "product_url" | "listing_url";
}) {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    async (
      signal: AbortSignal,
    ): Promise<{
      product: CreativePreviewProduct | null;
      article: CreativePreviewArticle | null;
    } | null> => {
      if (!open || !accountId) return null;
      try {
        if (format === "sponsored_content") {
          if (!articleId) return null;
          const page = await adminPartnerSponsorArticles(
            accountId,
            { article_id: articleId, limit: 1 },
            { signal },
          );
          const article = page.items.find((item) => item.id === articleId);
          if (!article) throw new Error("Choose an approved, published article.");
          return { product: null, article };
        }
        const result = await adminPartnerCreativePreview(
          accountId,
          productId ? { product_id: productId } : undefined,
          { signal },
        );
        return { product: result.product, article: null };
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) returnToLogin();
        throw error;
      }
    },
    [open, accountId, format, articleId, productId],
  );
  const result = useRequest(
    `creative-preview/${accountId}/${format}/${productId}/${articleId}/${open}/${revision}`,
    load,
  );
  return (
    <details
      className="space-y-4 rounded-lg border p-4"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm font-medium">Creative preview</summary>
      {open && (
        <AdCreativePreview
          format={format}
          destination={destination}
          product={result.data?.product}
          article={result.data?.article}
          loading={result.loading}
          error={!!result.error}
          onRetry={() => setRevision((value) => value + 1)}
        />
      )}
    </details>
  );
}

export function PartnerAdFormats({
  accountId,
  value,
  onChange,
  allowed,
  eligible,
  disabled,
  destination,
}: {
  accountId?: string;
  value: AdFormat[];
  onChange: (value: AdFormat[]) => void;
  allowed: AdFormat[];
  eligible: boolean;
  disabled?: boolean;
  destination: "product_url" | "listing_url";
}) {
  const [preview, setPreview] = useState<AdFormat>(value[0] ?? "feed");
  const selected = value.includes(preview) ? preview : (value[0] ?? "feed");
  return (
    <section aria-label="Automatic ad formats" className="space-y-4 border-t pt-6">
      <h2 className="font-semibold">Automatic ad formats</h2>
      <AutomaticAdFormats
        value={value}
        onChange={onChange}
        allowed={allowed}
        eligible={eligible}
        disabled={disabled}
      />
      {value.length > 1 && (
        <Select
          label="Preview format"
          value={selected}
          required
          onChange={(next) => setPreview(next as AdFormat)}
          options={adFormats
            .filter(
              (format) => value.includes(format.value) && automaticAdFormats.includes(format.value),
            )
            .map(({ value, label }) => ({ value, label }))}
        />
      )}
      {value.length > 0 && (
        <PartnerCreativePreview accountId={accountId} format={selected} destination={destination} />
      )}
    </section>
  );
}
