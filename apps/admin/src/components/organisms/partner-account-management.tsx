"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import type { AccountOut, AssetMetrics } from "@/lib/api/generated/models";
import { accountsPath, accountHref } from "./partner-accounts";

export function PartnerAccountManagement({
  account,
  asset,
  mode,
}: {
  account?: AccountOut;
  asset?: AssetMetrics;
  mode: "account" | "member" | "asset";
}) {
  const admin = useAdmin();
  const router = useRouter();
  const [status, setStatus] = useState<string>(
    asset?.status ?? account?.status ?? (mode === "asset" ? "draft" : "active"),
  );
  const [kind, setKind] = useState<string>(asset?.kind ?? "product");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const cancel = account
    ? accountHref(account.id) + (mode === "account" ? "" : "/related")
    : accountsPath;
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    const base = account ? `/${account.id}` : "";
    const path =
      mode === "member"
        ? `${base}/members`
        : mode === "asset"
          ? `${base}/assets${asset ? `/${asset.id}` : ""}`
          : base;
    const payload =
      mode === "member"
        ? { subject: form.get("subject") }
        : mode === "asset"
          ? {
              name: form.get("name"),
              kind,
              product_id: form.get("product_id") || null,
              status,
            }
          : {
              name: form.get("name"),
              tier: form.get("tier"),
              status,
              benefits: String(form.get("benefits") ?? "")
                .split("\n")
                .map((value) => value.trim())
                .filter(Boolean),
            };
    setBusy(true);
    setError(undefined);
    try {
      const response = await fetch(`/api/v1/admin/partner-accounts${path}`, {
        method: mode === "member" || asset || (mode === "account" && account) ? "PUT" : "POST",
        headers: { "content-type": "application/json", "x-csrf-token": admin.csrf_token },
        body: JSON.stringify(payload),
      });
      if (!response.ok)
        throw new Error(
          response.status === 422
            ? "Check the form values and catalog product ID."
            : "Could not save changes. Please retry.",
        );
      const saved = mode === "account" ? ((await response.json()) as AccountOut) : undefined;
      router.push(saved ? accountHref(saved.id) : cancel);
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error("Could not save changes."));
      setBusy(false);
    }
  }
  return (
    <form
      aria-label={
        mode === "account"
          ? "Partnership settings"
          : mode === "asset"
            ? "Asset settings"
            : "Partner membership"
      }
      aria-busy={busy}
      onSubmit={(event) => void save(event)}
      className="space-y-6 rounded-lg border bg-card p-6"
    >
      <RequestState error={error} />
      <div className="grid gap-6 sm:grid-cols-2">
        {mode === "member" ? (
          <Field
            label="Zitadel subject ID"
            name="subject"
            required
            disabled={busy}
            subtext="Assign the partner role in Zitadel first. Use the immutable subject ID; an email address does not grant access."
          >
            {(control) => <Input {...control} maxLength={200} />}
          </Field>
        ) : (
          <>
            <Field
              label={mode === "account" ? "Partner name" : "Asset name"}
              name="name"
              required
              disabled={busy}
            >
              {(control) => (
                <Input {...control} defaultValue={asset?.name ?? account?.name} maxLength={200} />
              )}
            </Field>
            {mode === "account" ? (
              <Field label="Partnership tier" name="tier" required disabled={busy}>
                {(control) => <Input {...control} defaultValue={account?.tier} maxLength={100} />}
              </Field>
            ) : (
              <>
                <Field label="Type" name="kind" required disabled={busy}>
                  {(control) => (
                    <Select
                      {...control}
                      label="Type"
                      value={kind}
                      onChange={setKind}
                      options={[
                        { value: "product", label: "Product placement" },
                        { value: "ad", label: "Ad" },
                      ]}
                    />
                  )}
                </Field>
                <Field
                  label="Catalog product ID"
                  name="product_id"
                  required={kind === "product"}
                  disabled={busy}
                  subtext="Required for product placements."
                >
                  {(control) => <Input {...control} defaultValue={asset?.product_id ?? ""} />}
                </Field>
              </>
            )}
            <Field label="Status" name="status" required disabled={busy}>
              {(control) => (
                <Select
                  {...control}
                  label="Status"
                  value={status}
                  onChange={setStatus}
                  options={(mode === "account"
                    ? ["active", "paused"]
                    : ["draft", "active", "paused", "ended"]
                  ).map((value) => ({
                    value,
                    label: value.charAt(0).toUpperCase() + value.slice(1),
                  }))}
                />
              )}
            </Field>
            {mode === "account" && (
              <Field
                label="Benefits, one per line"
                name="benefits"
                disabled={busy}
                className="sm:col-span-2"
              >
                {(control) => (
                  <Textarea {...control} defaultValue={(account?.benefits ?? []).join("\n")} />
                )}
              </Field>
            )}
          </>
        )}
      </div>
      <div className="flex flex-wrap justify-end gap-3 border-t pt-6">
        <Button type="button" variant="outline" disabled={busy} onClick={() => router.push(cancel)}>
          Cancel
        </Button>
        <Button type="submit" loading={busy}>
          {mode === "member"
            ? "Add member"
            : mode === "asset"
              ? asset
                ? "Save asset"
                : "Associate asset"
              : account
                ? "Save partnership"
                : "Create account"}
        </Button>
      </div>
    </form>
  );
}
