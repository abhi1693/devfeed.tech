"use client";

import { useRouter } from "next/navigation";
import {
  ProductPlacementDialog,
  parseProductPlacementPolicy,
  type AdFormat,
  type ProductPlacementRestrictions,
} from "@devfeed/ui/advertising-formats";

export function PartnerProductPlacements({
  accountId,
  productId,
  name,
  allowedFormats,
  csrfToken,
  disabled,
  onSaved,
  onUnavailable,
}: {
  accountId: string;
  productId: string;
  name: string;
  allowedFormats: AdFormat[];
  csrfToken: string;
  disabled?: boolean;
  onSaved: () => void;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  async function request(signal: AbortSignal, restrictions?: ProductPlacementRestrictions) {
    const response = await fetch(
      `/api/v1/partner/accounts/${accountId}/products/${productId}/promotion`,
      {
        method: restrictions ? "PUT" : "GET",
        cache: "no-store",
        signal,
        ...(restrictions
          ? {
              headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
              body: JSON.stringify(restrictions),
            }
          : {}),
      },
    );
    if (signal.aborted) throw new Error("Request cancelled.");
    if (response.status === 401) {
      router.replace("/login");
      onUnavailable();
    } else if (response.status === 404) onUnavailable();
    if (!response.ok)
      throw new Error(
        response.status === 403
          ? "Could not update this product. Refresh the page and try again."
          : "Could not load or save product placements. Please retry.",
      );
    const value = await response.json();
    if (value.account_id !== accountId || value.product_id !== productId)
      throw new Error("Could not confirm product placement settings.");
    return parseProductPlacementPolicy(value);
  }
  return (
    <ProductPlacementDialog
      name={name}
      allowedFormats={allowedFormats}
      disabled={disabled}
      onSaved={onSaved}
      loadPolicy={(signal) => request(signal)}
      savePolicy={(restrictions, signal) => request(signal, restrictions)}
    />
  );
}
