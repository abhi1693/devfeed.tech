"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  AdCreativePreview,
  AutomaticAdFormats,
  adFormats,
  automaticAdFormats,
  type AdFormat,
  type CreativePreviewProduct,
} from "@devfeed/ui/advertising-formats";
import { Select } from "@devfeed/ui/select";
import { Button } from "@devfeed/ui/button";
import type { Account } from "@/lib/types";
import { parseCreativePreview } from "@/lib/contracts";
import { isPartnerRequestCancelled } from "@/lib/partner-request";
import { usePartnerPreferenceSave } from "@/lib/use-partner-preference-save";

type Props = {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
};

export function PartnerAdFormats(props: Props) {
  return <AccountAdFormats key={props.account.id} {...props} />;
}

function AccountAdFormats({ account, csrfToken, onSaved, onUnavailable }: Props) {
  const [value, setValue] = useState<AdFormat[]>(account.ad_formats ?? ["feed"]);
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
    successMessage: "Ad formats saved.",
    errorMessage: "Could not save ad formats. Please retry.",
    confirmationMessage: "Could not confirm the saved ad formats. Refresh and try again.",
  });
  const allowed =
    account.allowed_ad_formats ??
    (account.tier === "bronze" ? ["feed"] : adFormats.map((format) => format.value));
  const eligible = allowed.includes("homepage_sponsorship");
  const [previewFormat, setPreviewFormat] = useState<AdFormat>(value[0] ?? "feed");
  const format = value.includes(previewFormat) ? previewFormat : (value[0] ?? "feed");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const previewKey = `${account.id}/${previewAttempt}`;
  const [preview, setPreview] = useState<{
    key: string;
    product: CreativePreviewProduct | null;
    error: boolean;
  }>({ key: "", product: null, error: false });
  useEffect(() => {
    if (!previewOpen) return;
    const controller = new AbortController();
    void request("creative-preview", {
      signal: controller.signal,
      parse: parseCreativePreview,
      errorMessage: "Could not load the creative preview.",
    })
      .then((product) => {
        if (!controller.signal.aborted) setPreview({ key: previewKey, product, error: false });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setPreview({ key: previewKey, product: null, error: true });
      });
    return () => controller.abort();
  }, [request, previewOpen, previewKey]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (eligible) await savePreferences({ ad_formats: value });
  }
  return (
    <section aria-label="Automatic ad formats" className="mt-6 space-y-4 border-t pt-6">
      <h2>Automatic ad formats</h2>
      <form
        aria-label="Ad format settings"
        onSubmit={save}
        aria-busy={saving}
        className="space-y-4"
      >
        <AutomaticAdFormats
          value={value}
          allowed={allowed}
          eligible={eligible}
          disabled={saving}
          onChange={(next) => {
            setValue(next);
            clearMessage();
          }}
        />
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
        {eligible && (
          <Button type="submit" loading={saving}>
            Save ad formats
          </Button>
        )}
      </form>
      {value.length > 1 && (
        <Select
          label="Preview format"
          required
          value={format}
          onChange={(next) => setPreviewFormat(next as AdFormat)}
          options={adFormats
            .filter((item) => value.includes(item.value) && automaticAdFormats.includes(item.value))
            .map(({ value, label }) => ({ value, label }))}
        />
      )}
      {value.length > 0 && (
        <details
          className="space-y-4 rounded-lg border p-4"
          onToggle={(event) => {
            setPreviewOpen(event.currentTarget.open);
            if (event.currentTarget.open) setPreviewAttempt((attempt) => attempt + 1);
          }}
        >
          <summary className="cursor-pointer text-sm font-medium">Creative preview</summary>
          {previewOpen && (
            <AdCreativePreview
              format={format}
              product={preview.product}
              destination={account.outbound_destination}
              loading={preview.key !== previewKey}
              error={preview.error}
              onRetry={() => setPreviewAttempt((attempt) => attempt + 1)}
            />
          )}
        </details>
      )}
    </section>
  );
}
