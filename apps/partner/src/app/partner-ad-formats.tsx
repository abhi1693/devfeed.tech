"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  AdCreativePreview,
  AutomaticAdFormats,
  adFormats,
  automaticAdFormats,
  parseCreativePreview,
  type AdFormat,
  type CreativePreviewProduct,
} from "@devfeed/ui/advertising-formats";
import { Select } from "@devfeed/ui/select";
import { Button } from "@/components/atoms/button";
import type { Account } from "@/lib/types";

export function PartnerAdFormats({
  account,
  csrfToken,
  onSaved,
  onUnavailable,
}: {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
}) {
  const router = useRouter();
  const [value, setValue] = useState<AdFormat[]>(account.ad_formats ?? ["feed"]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const access = useRef({ router, onUnavailable });
  useEffect(() => {
    access.current = { router, onUnavailable };
  }, [router, onUnavailable]);
  useEffect(() => () => request.current?.abort(), []);
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
    void (async () => {
      try {
        const response = await fetch(`/api/v1/partner/accounts/${account.id}/creative-preview`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (response.status === 401) {
          access.current.router.replace("/login");
          access.current.onUnavailable();
        } else if (response.status === 404) access.current.onUnavailable();
        if (!response.ok) throw new Error("Could not load the creative preview.");
        const product = parseCreativePreview(await response.json());
        if (!controller.signal.aborted) setPreview({ key: previewKey, product, error: false });
      } catch {
        if (!controller.signal.aborted) setPreview({ key: previewKey, product: null, error: true });
      }
    })();
    return () => controller.abort();
  }, [account.id, previewOpen, previewKey]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !eligible) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/v1/partner/accounts/${account.id}/preferences`, {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
        body: JSON.stringify({ ad_formats: value }),
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
            : "Could not save ad formats. Please retry.",
        );
      const saved = (await response.json()) as Account;
      if (
        saved.id !== account.id ||
        !Array.isArray(saved.ad_formats) ||
        !saved.ad_formats.every((format) => automaticAdFormats.includes(format))
      )
        throw new Error("Could not confirm the saved ad formats. Refresh and try again.");
      if (!controller.signal.aborted) {
        onSaved(saved);
        setMessage("Ad formats saved.");
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not save ad formats.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
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
            setMessage("");
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
