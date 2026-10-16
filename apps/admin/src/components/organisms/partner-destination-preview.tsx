"use client";

import { useCallback, useState } from "react";
import { ProductDestinationPreview } from "@devfeed/ui/product-destination-preview";
import { adminPartnerDestinationPreview } from "@/lib/api/generated/admin";
import { useRequest } from "@/lib/use-request";

type Props = { accountId?: string; destination: "product_url" | "listing_url" };

function AccountPreview({ accountId, destination }: Props & { accountId: string }) {
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => adminPartnerDestinationPreview(accountId, { signal }),
    [accountId],
  );
  const result = useRequest(`partner-destination-preview/${accountId}/${revision}`, load);
  return (
    <ProductDestinationPreview
      product={result.data?.product ?? null}
      destination={destination}
      loading={result.loading}
      error={result.error}
      onRetry={() => setRevision((value) => value + 1)}
    />
  );
}

export function PartnerDestinationPreview({ accountId, destination }: Props) {
  return accountId ? (
    <AccountPreview key={accountId} accountId={accountId} destination={destination} />
  ) : (
    <ProductDestinationPreview product={null} destination={destination} />
  );
}
