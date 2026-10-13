"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Select } from "@devfeed/ui/select";
import {
  ProductDestinationPreview,
  type DestinationPreviewProduct,
} from "@devfeed/ui/product-destination-preview";
import { Button } from "@devfeed/ui/button";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import type { Account, OutboundDestination } from "@/lib/types";
import { parseDestinationPreview } from "@/lib/contracts";
import { isPartnerRequestCancelled } from "@/lib/partner-request";
import { usePartnerPreferenceSave } from "@/lib/use-partner-preference-save";
import { PartnerAudience } from "./partner-audience";
import { PartnerAdFormats } from "./partner-ad-formats";

type Props = {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
};
export function PartnerPreferences(props: Props) {
  return <AccountPreferences key={props.account.id} {...props} />;
}

function AccountPreferences({ account, csrfToken, onSaved, onUnavailable }: Props) {
  const [destination, setDestination] = useState<OutboundDestination>(
    account.outbound_destination ?? "product_url",
  );
  const {
    request,
    save: savePreferences,
    saving,
    message,
    error,
    clearMessage,
  } = usePartnerPreferenceSave({
    accountId: account.id,
    csrfToken,
    onSaved,
    onUnavailable,
    successMessage: "Click destination saved.",
    errorMessage: "Could not save the click destination. Please retry.",
    confirmationMessage: "Could not confirm the saved destination. Refresh and try again.",
  });
  const [preview, setPreview] = useState<{
    product: DestinationPreviewProduct | null;
    loading: boolean;
    error: string;
  }>({ product: null, loading: true, error: "" });
  const [previewAttempt, setPreviewAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void request("destination-preview", {
      signal: controller.signal,
      parse: parseDestinationPreview,
      errorMessage: "Could not load the product preview. Please retry.",
    })
      .then((product) => {
        if (!controller.signal.aborted) setPreview({ product, loading: false, error: "" });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setPreview({
            product: null,
            loading: false,
            error:
              reason instanceof Error
                ? reason.message
                : "Could not load the product preview. Please retry.",
          });
      });
    return () => controller.abort();
  }, [request, previewAttempt]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await savePreferences({ outbound_destination: destination });
  }

  return (
    <section className="space-y-4" aria-label="Product promotion settings">
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <ResourcePanel title="Product promotion settings">
          <p className="mb-3 text-sm text-muted-foreground">
            Imported and manually added products inherit this account’s promotion type. Contextual
            placements require a relevant match.
          </p>
          <dl className="mb-4 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-1">
            <div>
              <dt className="text-muted-foreground">Default promotion</dt>
              <dd className="mt-1 font-medium">
                {account.effective_promotion_kind === "ad" ? "Sponsored" : "Product recommendation"}
                {account.promotion_kind_source === "tier"
                  ? " · inherited from tier"
                  : " · account setting"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Product acceptance</dt>
              <dd className="mt-1 font-medium">
                {account.approval_mode === "trusted"
                  ? "Trusted partner imports accepted automatically"
                  : "Automatic capability checks"}
              </dd>
            </div>
          </dl>
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
                clearMessage();
              }}
              disabled={saving}
              aria-describedby="partner-click-destination-help"
              options={[
                { value: "product_url", label: "Product website" },
                { value: "listing_url", label: "Partner listing" },
              ]}
            />
            <p id="partner-click-destination-help" className="muted text-sm">
              Product cards use this destination with DevFeed attribution. Partner listing requires
              an available listing URL.
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
          <div className="mt-4">
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
        </ResourcePanel>
        <div className="min-w-0 space-y-4">
          <PartnerAdFormats
            account={account}
            csrfToken={csrfToken}
            onSaved={onSaved}
            onUnavailable={onUnavailable}
          />
          <PartnerAudience
            account={account}
            csrfToken={csrfToken}
            onSaved={onSaved}
            onUnavailable={onUnavailable}
          />
        </div>
      </div>
    </section>
  );
}
