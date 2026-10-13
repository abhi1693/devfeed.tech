"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { productPath } from "@/lib/routes";
import {
  ProductPlacementEditor,
  parseProductPlacementPolicy,
  type AdFormat,
  type PlacementSurface,
  type ProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";
import { parseCreativePreview } from "../lib/contracts";
import { usePartnerRequest } from "../lib/partner-request";

export function PartnerProductPlacements({
  accountId,
  productId,
  name,
  allowedFormats,
  accountFormats,
  accountSurfaces,
  destination,
  csrfToken,
  onCancel,
  onSaved,
  onUnavailable,
}: {
  accountId: string;
  productId: string;
  name: string;
  allowedFormats: AdFormat[];
  accountFormats?: AdFormat[];
  accountSurfaces?: PlacementSurface[];
  destination?: "product_url" | "listing_url";
  csrfToken: string;
  onCancel?: () => void;
  onSaved: () => void;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const accountRequest = usePartnerRequest({ accountId, onUnavailable });
  const request = useCallback(
    (signal: AbortSignal, policy?: ProductPlacementPolicy) => {
      return accountRequest(`products/${productId}/promotion`, {
        method: policy ? "PUT" : "GET",
        signal,
        ...(policy
          ? {
              headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
              body: JSON.stringify(policy),
            }
          : {}),
        errorMessage: "Could not load or save product placements. Please retry.",
        invalidMessage: "Could not confirm product placement settings.",
        parse: (value) => {
          const row = value as { account_id?: unknown; product_id?: unknown } | null;
          if (row?.account_id !== accountId || row?.product_id !== productId)
            throw new Error("Unexpected product placement settings.");
          return parseProductPlacementPolicy(value);
        },
      });
    },
    [accountRequest, accountId, productId, csrfToken],
  );
  const loadPolicy = useCallback((signal: AbortSignal) => request(signal), [request]);
  const savePolicy = useCallback(
    (policy: ProductPlacementPolicy, signal: AbortSignal) => request(signal, policy),
    [request],
  );
  const loadPreview = useCallback(
    (signal: AbortSignal) =>
      accountRequest(`creative-preview?product_id=${encodeURIComponent(productId)}`, {
        signal,
        errorMessage: "Could not load the creative preview.",
        parse: (value) => {
          const product = parseCreativePreview(value);
          if (product && product.id !== productId) throw new Error("Unexpected preview product.");
          return product;
        },
      }),
    [accountRequest, productId],
  );
  return (
    <ProductPlacementEditor
      key={`${accountId}/${productId}`}
      name={name}
      allowedFormats={allowedFormats}
      accountFormats={accountFormats}
      accountSurfaces={accountSurfaces}
      destination={destination}
      previewInline
      onCancel={onCancel ?? (() => router.replace(productPath(productId, { accountId })))}
      onSaved={onSaved}
      loadPolicy={loadPolicy}
      savePolicy={savePolicy}
      loadPreview={loadPreview}
    />
  );
}
