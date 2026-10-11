"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { listRecords, getRecord } from "@/lib/resource-api";
import { EntityPicker, type EntityPickerSource } from "@/components/molecules/entity-picker";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import type { AccountOut, AssetMetrics } from "@/lib/api/generated/models";
import { type PartnershipTierOut, AccountInputTier } from "@/lib/api/generated/models";
import { adminPartnerToolsList } from "@/lib/api/generated/admin";
import { accountsPath, accountHref } from "./partner-accounts";
import { ApiError, returnToLogin } from "@/lib/api/client";
import {
  PromotionSurfaces,
  promotionSurfaces,
  type PromotionSurface,
} from "@/components/molecules/promotion-surfaces";

const memberUsers: EntityPickerSource = {
  key: "partner-member-users",
  list: (params, signal) => listRecords("users", { ...params, identity_only: "true" }, signal),
  get: (id, signal) => getRecord("users", id, signal),
};

const productCatalog = { label: "Products", singular: "Catalog product", title: "name" };
const catalogProducts: EntityPickerSource = {
  key: "partner-asset-products",
  list: async ({ q, offset, limit }, signal) => {
    const page = await adminPartnerToolsList({ q, offset, limit, status: "approved" }, { signal });
    return {
      ...page,
      items: page.items.map((product) => ({ id: product.id, name: product.name })),
    };
  },
  get: async (id, signal) => {
    const page = await adminPartnerToolsList({ product_id: id, limit: 1 }, { signal });
    if (!page.items[0]) throw new Error("Catalog product not found.");
    return { id: page.items[0].id, name: page.items[0].name };
  },
};

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
  const [productId, setProductId] = useState(asset?.product_id ?? "");
  const [memberUser, setMemberUser] = useState("");
  const [tier, setTier] = useState(account?.tier ?? "bronze");
  const [organicEnabled, setOrganicEnabled] = useState(account?.organic_enabled ?? true);
  const [autoInclude, setAutoInclude] = useState(account?.auto_include_products ?? true);
  const [surfaces, setSurfaces] = useState<PromotionSurface[]>(
    account?.promotion_surfaces ?? [...promotionSurfaces],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [tiers, setTiers] = useState<PartnershipTierOut[]>([]);
  const [tierError, setTierError] = useState<Error>();
  const [catalogRevision, setCatalogRevision] = useState(0);
  useEffect(() => {
    if (mode !== "account") return;
    const controller = new AbortController();
    fetch("/api/v1/admin/partner-accounts/tiers", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) {
          returnToLogin();
          throw new ApiError(401);
        }
        if (!response.ok) throw new Error("Could not load tier benefits.");
        return response.json() as Promise<PartnershipTierOut[]>;
      })
      .then((catalog) => {
        if (!controller.signal.aborted) {
          setTiers(catalog);
          setTierError(undefined);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setTierError(reason);
      });
    return () => controller.abort();
  }, [mode, catalogRevision]);
  const selectedBenefits = tiers.find((item) => item.tier === tier)?.benefits;
  const cancel = account
    ? accountHref(account.id) + (mode === "account" ? "" : "/related")
    : accountsPath;
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    if (mode === "account" && !surfaces.length) {
      setError(new Error("Select at least one placement surface."));
      return;
    }
    const base = account ? `/${account.id}` : "";
    const path =
      mode === "member"
        ? `${base}/members`
        : mode === "asset"
          ? `${base}/assets${asset ? `/${asset.id}` : ""}`
          : base;
    const payload =
      mode === "member"
        ? { user_id: memberUser }
        : mode === "asset"
          ? {
              name: form.get("name"),
              kind,
              product_id: productId || null,
              status,
            }
          : {
              name: form.get("name"),
              tier,
              status,
              organic_enabled: organicEnabled,
              auto_include_products: autoInclude,
              promotion_surfaces: surfaces,
              frequency_cap: Number(form.get("frequency_cap")),
            };
    setBusy(true);
    setError(undefined);
    try {
      const response = await fetch(`/api/v1/admin/partner-accounts${path}`, {
        method: mode === "member" || asset || (mode === "account" && account) ? "PUT" : "POST",
        headers: { "content-type": "application/json", "x-csrf-token": admin.csrf_token },
        body: JSON.stringify(payload),
      });
      if (response.status === 401) {
        returnToLogin();
        throw new ApiError(401);
      }
      if (response.status === 403) throw new ApiError(403);
      if (!response.ok)
        throw new Error(
          response.status === 422
            ? "Check the form values and selected catalog product."
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
            label="User"
            name="user_id"
            required
            disabled={busy}
            subtext="Users appear after partner or reader sign-in. The partner role in Zitadel is also required to access this account."
          >
            {(control) => (
              <EntityPicker
                {...control}
                resource="users"
                source={memberUsers}
                label="User"
                value={memberUser}
                onChange={setMemberUser}
              />
            )}
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
                {(control) => (
                  <Select
                    {...control}
                    label="Partnership tier"
                    value={tier}
                    onChange={(value) => setTier(value as typeof tier)}
                    options={Object.values(AccountInputTier).map((value) => ({
                      value,
                      label: value.charAt(0).toUpperCase() + value.slice(1),
                    }))}
                  />
                )}
              </Field>
            ) : (
              <>
                <Field label="Type" name="kind" required disabled={busy || asset?.automatic}>
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
                  label="Catalog product"
                  name="product_id"
                  required={kind === "product"}
                  disabled={busy || asset?.automatic}
                >
                  {(control) => (
                    <EntityPicker
                      {...control}
                      source={catalogProducts}
                      catalog={productCatalog}
                      value={productId}
                      onChange={setProductId}
                    />
                  )}
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
              <section
                aria-label="Selected tier benefits"
                aria-live="polite"
                className="space-y-2 sm:col-span-2"
              >
                <h2 className="text-sm font-medium">Included benefits</h2>
                <RequestState
                  loading={!selectedBenefits && !tierError}
                  error={tierError}
                  retry={() => setCatalogRevision((n) => n + 1)}
                />
                {selectedBenefits && (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                    {selectedBenefits.map((benefit) => (
                      <li key={benefit}>{benefit}</li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </>
        )}
      </div>
      {mode === "account" && (
        <section aria-label="Promotion defaults" className="space-y-5 border-t pt-6">
          <div className="space-y-1">
            <h2 className="font-semibold">Promotion defaults</h2>
            <p className="text-sm text-muted-foreground">
              Products imported through this account inherit these settings. Every appearance must
              match the reader’s context. Paid promotion requires an active campaign.
            </p>
          </div>
          <label className="flex items-start gap-3 text-sm">
            <Input
              type="checkbox"
              checked={organicEnabled}
              disabled={busy}
              onChange={(event) => setOrganicEnabled(event.target.checked)}
            />
            <span>
              Allow ordinary recommendations
              <span className="mt-1 block text-muted-foreground">
                Relevant products can appear without a paid campaign, on any partnership tier.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3 text-sm">
            <Input
              type="checkbox"
              checked={autoInclude}
              disabled={busy}
              onChange={(event) => setAutoInclude(event.target.checked)}
            />
            <span>
              Automatically include imported products
              <span className="mt-1 block text-muted-foreground">
                Current and newly imported products become eligible after qualification. You can
                exclude individual products from the Products tab.
              </span>
            </span>
          </label>
          <PromotionSurfaces value={surfaces} onChange={setSurfaces} disabled={busy} />
          <Field
            label="Product exposure limit"
            name="frequency_cap"
            required
            disabled={busy}
            subtext="Maximum viewable impressions of the same product per UTC day in each web reader or extension installation."
          >
            {(control) => (
              <Input
                {...control}
                type="number"
                min={1}
                max={20}
                defaultValue={account?.frequency_cap ?? 3}
                className="max-w-32"
              />
            )}
          </Field>
        </section>
      )}
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
