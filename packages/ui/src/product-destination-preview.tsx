"use client";

import { ArrowUpRight } from "lucide-react";
import { RetryButton } from "./retry-button";

export type DestinationPreviewProduct = {
  id: string;
  name: string;
  product_url: string;
  listing_url: string | null;
};

export function ProductDestinationPreview({
  product,
  destination,
  loading = false,
  error,
  onRetry,
}: {
  product: DestinationPreviewProduct | null;
  destination: "product_url" | "listing_url";
  loading?: boolean;
  error?: Error | string;
  onRetry?: () => void;
}) {
  const url = product?.[destination];
  return (
    <section
      className="min-w-0 space-y-2"
      aria-label="Click destination preview"
      aria-busy={loading}
    >
      <h3 className="text-sm font-medium">URL preview</h3>
      {loading ? (
        <p
          role="status"
          className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground"
        >
          Loading product preview…
        </p>
      ) : error ? (
        <div role="alert" className="space-y-3 rounded-lg border bg-muted/30 p-4 text-sm">
          <p>Could not load the product preview.</p>
          {onRetry && <RetryButton label="Retry preview" onRetry={onRetry} />}
        </div>
      ) : product ? (
        url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            referrerPolicy="no-referrer"
            title={product.name}
            className="flex min-w-0 items-start justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2 text-sm text-primary hover:underline"
          >
            <span className="min-w-0 break-all">{url}</span>
            <ArrowUpRight aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          </a>
        ) : (
          <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
            This product has no available partner listing.
          </p>
        )
      ) : (
        <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          A preview will appear when this account has an accepted product.
        </p>
      )}
    </section>
  );
}
