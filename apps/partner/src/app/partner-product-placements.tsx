"use client";

import { useCallback } from "react";
import {
  ProductPlacementDialog,
  parseProductPlacementPolicy,
  type AdFormat,
  type PlacementSurface,
  type ProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";
import { parseCreativePreview } from "../lib/contracts";
import { usePartnerAccountRequest } from "../lib/partner-request";

export function PartnerProductPlacements({
  accountId,
  productId,
  name,
  allowedFormats,
  accountFormats,
  accountSurfaces,
  destination,
  csrfToken,
  disabled,
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
  disabled?: boolean;
  onSaved: () => void;
  onUnavailable: () => void;
}) {
  const accountRequest = usePartnerAccountRequest({ accountId, onUnavailable });
  function request(signal: AbortSignal, policy?: ProductPlacementPolicy) {
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
  }
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
    <ProductPlacementDialog
      key={`${accountId}/${productId}`}
      name={name}
      allowedFormats={allowedFormats}
      accountFormats={accountFormats}
      accountSurfaces={accountSurfaces}
      destination={destination}
      disabled={disabled}
      onSaved={onSaved}
      loadPolicy={(signal) => request(signal)}
      savePolicy={(restrictions, signal) => request(signal, restrictions)}
      loadPreview={loadPreview}
    />
  );
}
