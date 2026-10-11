"use client";

import { useCallback, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { EntityPicker, type EntityPickerSource } from "@/components/molecules/entity-picker";
import { Field } from "@/components/molecules/field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import {
  PromotionSurfaces,
  promotionSurfaces,
  type PromotionSurface,
} from "@/components/molecules/promotion-surfaces";
import { useAdmin } from "@/components/molecules/admin-session";
import {
  adminPartnerCampaignCreate,
  adminPartnerCampaignGet,
  adminPartnerCampaignUpdate,
  adminPartnerInventoryList,
} from "@/lib/api/generated/admin";
import type { AccountOut, CampaignInput, CampaignOut } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { useRequest } from "@/lib/use-request";
import { accountHref } from "./partner-accounts";

const productCatalog = { label: "Account products", singular: "Account product", title: "name" };

function localDate(value: Date) {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

export function PartnerCampaignPage({
  account,
  campaignId,
}: {
  account: AccountOut;
  campaignId?: string;
}) {
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) =>
      campaignId
        ? adminPartnerCampaignGet(account.id, campaignId, { signal })
        : Promise.resolve(null),
    [account.id, campaignId],
  );
  const result = useRequest(`partner-campaign/${account.id}/${campaignId}/${revision}`, load);
  if (campaignId && (result.loading || result.error))
    return (
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
    );
  if (campaignId && !result.data)
    return <p role="alert">This campaign is unavailable for this account.</p>;
  return (
    <PartnerCampaignForm account={account} campaign={result.data ?? undefined} key={campaignId} />
  );
}

function PartnerCampaignForm({
  account,
  campaign,
}: {
  account: AccountOut;
  campaign?: CampaignOut;
}) {
  const admin = useAdmin();
  const router = useRouter();
  const [status, setStatus] = useState<NonNullable<CampaignInput["status"]>>(
    campaign?.status ?? "draft",
  );
  const [targeting, setTargeting] = useState<NonNullable<CampaignInput["targeting"]>>(
    campaign?.targeting ?? "all",
  );
  const [products, setProducts] = useState(campaign?.product_ids ?? []);
  const [topics, setTopics] = useState(campaign?.topic_ids ?? []);
  const [surfaces, setSurfaces] = useState<PromotionSurface[]>(
    campaign?.surfaces ?? account.promotion_surfaces ?? [...promotionSurfaces],
  );
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [dates] = useState(() => {
    const start = campaign?.starts_at ? new Date(campaign.starts_at) : new Date();
    const end = campaign?.ends_at
      ? new Date(campaign.ends_at)
      : new Date(start.getTime() + 30 * 86_400_000);
    return { start: localDate(start), end: localDate(end) };
  });
  const source = useMemo<EntityPickerSource>(
    () => ({
      key: `campaign-inventory/${account.id}`,
      list: async ({ q, offset, limit }, signal) => {
        const page = await adminPartnerInventoryList(account.id, { q, offset, limit }, { signal });
        return {
          ...page,
          offset: offset ?? 0,
          limit: limit ?? 25,
          items: page.items.map((product) => ({ ...product })),
        };
      },
      get: async (id, signal) => {
        const page = await adminPartnerInventoryList(
          account.id,
          { product_id: id, limit: 1 },
          { signal },
        );
        const product = page.items.find((item) => item.id === id);
        if (!product) throw new Error("Product is unavailable for this account.");
        return { ...product };
      },
    }),
    [account.id],
  );
  const cancel = `${accountHref(account.id)}/campaigns`;
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    const start = new Date(String(form.get("starts_at")));
    const end = new Date(String(form.get("ends_at")));
    const technologies = String(form.get("technologies") ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start) {
      setError(new Error("Choose an end date after the start date."));
      return;
    }
    if (!surfaces.length) {
      setError(new Error("Select at least one placement surface."));
      return;
    }
    if (targeting === "selected" && !products.length) {
      setError(new Error("Select at least one product from this account."));
      return;
    }
    if (targeting === "filters" && !topics.length && !technologies.length) {
      setError(new Error("Choose a topic or enter a technology for product targeting."));
      return;
    }
    const payload: CampaignInput = {
      name: String(form.get("name")).trim(),
      status,
      targeting,
      product_ids: targeting === "selected" ? products : [],
      topic_ids: targeting === "filters" ? topics : [],
      technologies: targeting === "filters" ? technologies : [],
      surfaces,
      starts_at: start.toISOString(),
      ends_at: end.toISOString(),
      priority: Number(form.get("priority")),
      daily_impression_limit: form.get("daily_impression_limit")
        ? Number(form.get("daily_impression_limit"))
        : null,
    };
    setBusy(true);
    setError(undefined);
    try {
      const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
      if (campaign) await adminPartnerCampaignUpdate(account.id, campaign.id, payload, options);
      else await adminPartnerCampaignCreate(account.id, payload, options);
      router.push(cancel);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) returnToLogin();
      setError(reason instanceof Error ? reason : new Error("Could not save campaign."));
      setBusy(false);
    }
  }
  return (
    <form
      aria-label="Paid campaign settings"
      aria-busy={busy}
      onSubmit={(event) => void save(event)}
      className="space-y-6 rounded-lg border bg-card p-6"
    >
      <div className="space-y-1">
        <h2 className="font-semibold">
          {campaign ? "Edit paid campaign" : "Create paid campaign"}
        </h2>
        <p className="text-sm text-muted-foreground">
          Use a campaign for an agreed paid promotion. Active campaigns run within their dates and
          select only qualified, relevant products. Account pauses, product exclusions and account
          placement settings continue to apply.
        </p>
      </div>
      <RequestState error={error} />
      <ValidationErrors error={error} />
      <div className="grid gap-6 sm:grid-cols-2">
        <Field label="Campaign name" name="name" required disabled={busy}>
          {(control) => <Input {...control} maxLength={200} defaultValue={campaign?.name} />}
        </Field>
        <Field label="Campaign status" name="status" required disabled={busy}>
          {(control) => (
            <Select
              {...control}
              label="Campaign status"
              value={status}
              onChange={(value) => setStatus(value as typeof status)}
              options={[
                { value: "draft", label: "Draft" },
                { value: "active", label: "Active" },
                { value: "paused", label: "Paused" },
                { value: "ended", label: "Ended" },
              ]}
            />
          )}
        </Field>
        <Field label="Starts" name="starts_at" required disabled={busy} subtext="Your local time.">
          {(control) => <Input {...control} type="datetime-local" defaultValue={dates.start} />}
        </Field>
        <Field label="Ends" name="ends_at" required disabled={busy} subtext="Your local time.">
          {(control) => <Input {...control} type="datetime-local" defaultValue={dates.end} />}
        </Field>
        <Field
          label="Campaign priority"
          name="priority"
          required
          disabled={busy}
          subtext="Relevance ranks sponsors first. Higher priority breaks ties between equally relevant campaigns."
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              max={100}
              defaultValue={campaign?.priority ?? 50}
            />
          )}
        </Field>
        <Field
          label="Daily impression limit"
          name="daily_impression_limit"
          disabled={busy}
          subtext="Campaign-wide limit per UTC day. Leave empty for no daily limit."
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={1}
              defaultValue={campaign?.daily_impression_limit ?? ""}
            />
          )}
        </Field>
      </div>
      <section className="space-y-5 border-t pt-6" aria-label="Campaign product targeting">
        <Field label="Product targeting" name="targeting" required disabled={busy}>
          {(control) => (
            <Select
              {...control}
              label="Product targeting"
              value={targeting}
              onChange={(value) => setTargeting(value as typeof targeting)}
              options={[
                { value: "all", label: "All eligible account products" },
                { value: "selected", label: "Selected account products" },
                { value: "filters", label: "Products matching topics or technologies" },
              ]}
            />
          )}
        </Field>
        {targeting === "all" && (
          <p className="text-sm text-muted-foreground">
            Covers eligible products from this account’s import connections, including newly
            imported products after qualification. Automatic inclusion must be enabled on the
            account.
          </p>
        )}
        {targeting === "selected" && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Choose from this account’s imported products. Selected products can participate when
              automatic inclusion is off; excluded products remain blocked.
            </p>
            {products.map((productId, index) => (
              <div className="flex items-end gap-2" key={productId}>
                <Field label={`Selected product ${index + 1}`} disabled={busy} className="flex-1">
                  {(control) => (
                    <EntityPicker
                      {...control}
                      label={`Selected product ${index + 1}`}
                      source={source}
                      catalog={productCatalog}
                      value={productId}
                      onChange={(next) =>
                        setProducts((current) =>
                          [...new Set(current.map((id) => (id === productId ? next : id)))].filter(
                            Boolean,
                          ),
                        )
                      }
                    />
                  )}
                </Field>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={busy}
                  aria-label={`Remove selected product ${index + 1}`}
                  onClick={() => setProducts((current) => current.filter((id) => id !== productId))}
                >
                  <X aria-hidden />
                </Button>
              </div>
            ))}
            <Field label="Add account product" disabled={busy}>
              {(control) => (
                <EntityPicker
                  {...control}
                  label="Add account product"
                  source={source}
                  catalog={productCatalog}
                  value=""
                  onChange={(id) => id && setProducts((current) => [...new Set([...current, id])])}
                />
              )}
            </Field>
          </div>
        )}
        {targeting === "filters" && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Products must match at least one selected topic and at least one entered technology
              when both are supplied. Within each list, any match qualifies. Each appearance must
              also match its article, topic page or reader context.
            </p>
            {topics.map((topicId, index) => (
              <div className="flex items-end gap-2" key={topicId}>
                <Field label={`Selected topic ${index + 1}`} disabled={busy} className="flex-1">
                  {(control) => (
                    <EntityPicker
                      {...control}
                      label={`Selected topic ${index + 1}`}
                      resource="topics"
                      status="active"
                      value={topicId}
                      onChange={(next) =>
                        setTopics((current) =>
                          [...new Set(current.map((id) => (id === topicId ? next : id)))].filter(
                            Boolean,
                          ),
                        )
                      }
                    />
                  )}
                </Field>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={busy}
                  aria-label={`Remove selected topic ${index + 1}`}
                  onClick={() => setTopics((current) => current.filter((id) => id !== topicId))}
                >
                  <X aria-hidden />
                </Button>
              </div>
            ))}
            <Field label="Add topic" disabled={busy}>
              {(control) => (
                <EntityPicker
                  {...control}
                  label="Add topic"
                  resource="topics"
                  status="active"
                  value=""
                  onChange={(id) => id && setTopics((current) => [...new Set([...current, id])])}
                />
              )}
            </Field>
            <Field
              label="Technologies"
              name="technologies"
              disabled={busy}
              subtext="Comma-separated verified technology names, for example Kubernetes, PostgreSQL."
            >
              {(control) => (
                <Input {...control} defaultValue={campaign?.technologies?.join(", ")} />
              )}
            </Field>
          </div>
        )}
      </section>
      <PromotionSurfaces value={surfaces} onChange={setSurfaces} disabled={busy} />
      <div className="flex flex-wrap justify-end gap-3 border-t pt-6">
        <Button type="button" variant="outline" disabled={busy} onClick={() => router.push(cancel)}>
          Cancel
        </Button>
        <Button type="submit" loading={busy}>
          {campaign ? "Save campaign" : "Create campaign"}
        </Button>
      </div>
    </form>
  );
}
