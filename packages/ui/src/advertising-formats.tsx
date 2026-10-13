"use client";

/* eslint-disable @next/next/no-img-element -- Previews use managed product/article media. */
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Dialog } from "radix-ui";
import { Select } from "./select";

export const adFormats = [
  {
    value: "feed",
    label: "Feed",
    description: "Product cards in reader feeds and relevant article or topic placements.",
  },
  {
    value: "homepage_sponsorship",
    label: "Homepage sponsorship",
    description: "A compact sponsor placement on the main homepage.",
  },
  {
    value: "sponsored_content",
    label: "Sponsored content",
    description: "An approved article promoted in reader feeds through a dated campaign.",
  },
  {
    value: "engagement",
    label: "Engagement",
    description: "A contextual product card within relevant feeds and article previews.",
  },
] as const;
export type AdFormat = (typeof adFormats)[number]["value"];
export const automaticAdFormats: AdFormat[] = ["feed", "homepage_sponsorship", "engagement"];
export const adFormatLabel = (format: string) =>
  adFormats.find((item) => item.value === format)?.label ?? format.replaceAll("_", " ");

export function AutomaticAdFormats({
  value,
  onChange,
  allowed,
  disabled = false,
  eligible = true,
}: {
  value: readonly AdFormat[];
  onChange?: (value: AdFormat[]) => void;
  allowed: readonly AdFormat[];
  disabled?: boolean;
  eligible?: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="mb-2 text-sm font-medium">Enabled automatic formats</legend>
      {!eligible && (
        <p className="text-sm text-muted-foreground">
          Ad formats require Silver or higher. Saved choices are retained.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        {adFormats
          .filter((format) => automaticAdFormats.includes(format.value))
          .map((format) => (
            <label
              key={format.value}
              aria-label={format.label}
              className="flex items-start gap-3 rounded-lg border p-3 text-sm"
            >
              <input
                type="checkbox"
                className="mt-1 shrink-0"
                checked={value.includes(format.value)}
                disabled={!onChange || !eligible || !allowed.includes(format.value)}
                onChange={(event) =>
                  onChange?.(
                    automaticAdFormats.filter((item) =>
                      item === format.value ? event.target.checked : value.includes(item),
                    ),
                  )
                }
              />
              <span>
                <span className="block font-medium">{format.label}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {format.description}
                </span>
              </span>
            </label>
          ))}
      </div>
      <p className="text-sm text-muted-foreground">
        Applied when the account’s default promotion is Sponsored. Clear all choices to turn off
        automatic paid placements. Sponsored content uses a dated campaign with an approved article.
      </p>
    </fieldset>
  );
}

export type CreativePreviewProduct = {
  id: string;
  name: string;
  description: string;
  logo_url: string | null;
  image_url: string | null;
  product_url: string;
  listing_url: string | null;
};
export type CreativePreviewArticle = {
  id: string;
  title: string;
  slug: string;
  summary?: string | null;
  canonical_url?: string;
  image_url?: string | null;
};

export const placementSurfaces = [
  { value: "article", label: "Article pages" },
  { value: "topic", label: "Topic and category pages" },
  { value: "feed", label: "Reader feeds" },
] as const;
export type PlacementSurface = (typeof placementSurfaces)[number]["value"];
export type ProductPlacementRestrictions = {
  ad_formats: AdFormat[] | null;
  promotion_surfaces: PlacementSurface[] | null;
};
export type ProductPlacementPolicy = ProductPlacementRestrictions & { excluded: boolean };
function sameSelection(left: string[] | null, right: string[] | null) {
  return left === null || right === null
    ? left === right
    : left.length === right.length && left.every((item) => right.includes(item));
}
function sameProductPolicy(left: ProductPlacementPolicy, right: ProductPlacementPolicy) {
  return (
    left.excluded === right.excluded &&
    sameSelection(left.ad_formats, right.ad_formats) &&
    sameSelection(left.promotion_surfaces, right.promotion_surfaces)
  );
}
export function parseProductPlacementPolicy(value: unknown): ProductPlacementPolicy {
  if (!value || typeof value !== "object") throw new Error("Invalid product placement settings.");
  const row = value as ProductPlacementPolicy;
  if (
    typeof row.excluded !== "boolean" ||
    !(
      row.ad_formats === null ||
      (Array.isArray(row.ad_formats) &&
        row.ad_formats.every((format) => adFormats.some((item) => item.value === format)))
    ) ||
    !(
      row.promotion_surfaces === null ||
      (Array.isArray(row.promotion_surfaces) &&
        row.promotion_surfaces.every((surface) =>
          placementSurfaces.some((item) => item.value === surface),
        ))
    )
  )
    throw new Error("Invalid product placement settings.");
  return row;
}

type ProductPlacementEditorProps = {
  name: string;
  allowedFormats: AdFormat[];
  accountFormats?: AdFormat[];
  accountSurfaces?: PlacementSurface[];
  destination?: "product_url" | "listing_url";
  loadPolicy: (signal: AbortSignal) => Promise<ProductPlacementPolicy>;
  savePolicy: (
    policy: ProductPlacementPolicy,
    signal: AbortSignal,
  ) => Promise<ProductPlacementPolicy>;
  loadPreview?: (signal: AbortSignal) => Promise<CreativePreviewProduct | null>;
  onSaved?: () => void;
};

export function ProductPlacementDialog(
  props: ProductPlacementEditorProps & { disabled?: boolean },
) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          disabled={props.disabled}
          aria-label={`Edit placements for ${props.name}`}
          className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
        >
          Placements
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-5 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">{props.name} placements</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">
            Inherit account defaults or restrict this product’s formats and locations. Restrictions
            also apply to dated campaigns and cannot enable an account-disabled automatic format.
          </Dialog.Description>
          <ProductPlacementEditor
            {...props}
            onCancel={() => setOpen(false)}
            cancelLabel="Close placements"
            onSaved={() => {
              setOpen(false);
              props.onSaved?.();
            }}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ProductPlacementEditor({
  allowedFormats,
  accountFormats = allowedFormats,
  accountSurfaces = placementSurfaces.map((surface) => surface.value),
  destination,
  loadPolicy,
  savePolicy,
  loadPreview,
  onSaved,
  onCancel,
  cancelLabel = "Cancel",
  previewInline = false,
}: ProductPlacementEditorProps & {
  onCancel: () => void;
  cancelLabel?: string;
  previewInline?: boolean;
}) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<ProductPlacementPolicy | null>(null);
  const [excluded, setExcluded] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(previewInline);
  const [formatMode, setFormatMode] = useState("inherit");
  const [surfaceMode, setSurfaceMode] = useState("inherit");
  const [formats, setFormats] = useState<AdFormat[]>([]);
  const [surfaces, setSurfaces] = useState<PlacementSurface[]>([]);
  const request = useRef<AbortController | null>(null);
  // The caller keys this editor by account and product. Parent refreshes must
  // preserve an open draft even when they create new callback/format arrays.
  const initial = useRef({ loadPolicy, allowedFormats, previewInline });
  useEffect(() => () => request.current?.abort(), []);
  const draft: ProductPlacementPolicy = {
    excluded,
    ad_formats: formatMode === "inherit" ? null : formats,
    promotion_surfaces: surfaceMode === "inherit" ? null : surfaces,
  };
  const dirty = policy && !sameProductPolicy(policy, draft);
  const effectiveFormats = excluded
    ? []
    : automaticAdFormats.filter(
        (format) =>
          allowedFormats.includes(format) &&
          accountFormats.includes(format) &&
          (draft.ad_formats === null || draft.ad_formats.includes(format)),
      );
  const effectiveSurfaces = excluded
    ? []
    : placementSurfaces.filter(
        ({ value }) =>
          accountSurfaces.includes(value) &&
          (draft.promotion_surfaces === null || draft.promotion_surfaces.includes(value)),
      );
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    setPolicy(null);
    setPreviewOpen(initial.current.previewInline);
    try {
      const next = await initial.current.loadPolicy(controller.signal);
      if (controller.signal.aborted) return;
      setPolicy(next);
      setExcluded(next.excluded);
      setFormatMode(next.ad_formats === null ? "inherit" : "custom");
      setSurfaceMode(next.promotion_surfaces === null ? "inherit" : "custom");
      setFormats(next.ad_formats ?? [...initial.current.allowedFormats]);
      setSurfaces(next.promotion_surfaces ?? placementSurfaces.map((surface) => surface.value));
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not load product placements.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !policy) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setError("");
    try {
      const saved = await savePolicy(draft, controller.signal);
      if (!controller.signal.aborted) {
        if (!sameProductPolicy(parseProductPlacementPolicy(saved), draft))
          throw new Error(
            "Could not confirm saved product placements. Review your changes and retry.",
          );
        setPolicy(saved);
        onSaved?.();
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not save product placements.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  return (
    <div>
      {loading ? (
        <p role="status" className="py-6 text-sm">
          Loading product placements…
        </p>
      ) : (
        policy && (
          <form
            aria-label="Product placement settings"
            onSubmit={save}
            aria-busy={saving}
            className={
              previewInline ? "mt-5 grid items-start gap-6 lg:grid-cols-2" : "mt-5 space-y-5"
            }
          >
            <div className="space-y-5">
              <Select
                label="Product availability"
                required
                value={excluded ? "excluded" : "allowed"}
                disabled={saving}
                onChange={(value) => setExcluded(value === "excluded")}
                options={[
                  { value: "allowed", label: "Allow this product" },
                  { value: "excluded", label: "Exclude this product" },
                ]}
              />
              {excluded && (
                <p className="text-sm text-muted-foreground">
                  {policy.excluded
                    ? "This product is excluded."
                    : "This product will be excluded after saving."}{" "}
                  Exclusion blocks placements for this product, including dated product campaigns.
                  Format and location restrictions are retained.
                </p>
              )}
              <Select
                label="Product formats"
                required
                value={formatMode}
                disabled={saving}
                onChange={setFormatMode}
                options={[
                  { value: "inherit", label: "Inherit account formats" },
                  { value: "custom", label: "Choose product formats" },
                ]}
              />
              {formatMode === "custom" && (
                <fieldset disabled={saving} className="space-y-2">
                  <legend className="mb-2 text-sm font-medium">Allowed product formats</legend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {adFormats.map((format) => (
                      <label key={format.value} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={formats.includes(format.value)}
                          disabled={!allowedFormats.includes(format.value)}
                          onChange={(event) =>
                            setFormats((current) =>
                              event.target.checked
                                ? [...current, format.value]
                                : current.filter((item) => item !== format.value),
                            )
                          }
                        />
                        {format.label}
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    No formats selected blocks this product in every format. Sponsored content
                    requires an approved-article campaign.
                  </p>
                </fieldset>
              )}
              <Select
                label="Product placements"
                required
                value={surfaceMode}
                disabled={saving}
                onChange={setSurfaceMode}
                options={[
                  { value: "inherit", label: "Inherit account placements" },
                  { value: "custom", label: "Choose product placements" },
                ]}
              />
              {surfaceMode === "custom" && (
                <fieldset disabled={saving} className="space-y-2">
                  <legend className="mb-2 text-sm font-medium">Allowed product placements</legend>
                  <div className="flex flex-wrap gap-3">
                    {placementSurfaces.map((surface) => (
                      <label key={surface.value} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={surfaces.includes(surface.value)}
                          onChange={(event) =>
                            setSurfaces((current) =>
                              event.target.checked
                                ? [...current, surface.value]
                                : current.filter((item) => item !== surface.value),
                            )
                          }
                        />
                        {surface.label}
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    No placements selected blocks this product everywhere.
                  </p>
                </fieldset>
              )}
              <div className="space-y-2 rounded-lg bg-muted p-3 text-sm">
                <p role="status" className="font-medium">
                  {dirty ? "Unsaved changes" : "Saved product settings"}
                </p>
                <p>
                  Allowed automatic formats after saving:{" "}
                  {effectiveFormats.map(adFormatLabel).join(", ") || "None"}.
                </p>
                <p>
                  Allowed locations after saving:{" "}
                  {effectiveSurfaces.map((surface) => surface.label).join(", ") || "None"}.
                </p>
                <p className="text-xs text-muted-foreground">
                  Inherited settings follow future account changes. Product restrictions also limit
                  dated campaigns, which choose their own formats. Allowing a product does not
                  guarantee delivery: account status, automatic inclusion, approval, ownership,
                  targeting, and available capacity still apply. Each format also supports specific
                  locations.
                </p>
              </div>
            </div>
            {loadPreview && (
              <div
                className={
                  previewInline ? "space-y-3 lg:sticky lg:top-6" : "space-y-3 border-t pt-4"
                }
              >
                {previewInline ? (
                  <h3 className="text-sm font-semibold">Creative preview</h3>
                ) : (
                  <button
                    type="button"
                    className="text-sm underline"
                    aria-expanded={previewOpen}
                    onClick={() => setPreviewOpen((current) => !current)}
                  >
                    {previewOpen ? "Hide product preview" : "Preview this product"}
                  </button>
                )}
                {previewOpen && (
                  <ProductPlacementPreview
                    allowedFormats={allowedFormats}
                    initialFormat={
                      effectiveFormats[0] ??
                      automaticAdFormats.find((format) => formats.includes(format)) ??
                      "feed"
                    }
                    destination={destination}
                    load={loadPreview}
                  />
                )}
              </div>
            )}
            {error && (
              <p
                role="alert"
                className={
                  previewInline
                    ? "text-sm text-destructive lg:col-span-2"
                    : "text-sm text-destructive"
                }
              >
                {error}
              </p>
            )}
            <div
              className={
                previewInline
                  ? "flex flex-wrap justify-end gap-3 border-t pt-5 lg:col-span-2"
                  : "flex flex-wrap justify-end gap-3"
              }
            >
              <button
                type="button"
                className="rounded-md border px-3 py-2 text-sm"
                disabled={saving}
                onClick={onCancel}
              >
                {cancelLabel}
              </button>
              <button
                type="submit"
                disabled={saving}
                className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save product placements"}
              </button>
            </div>
          </form>
        )
      )}
      {!policy && !loading && (
        <div className="mt-5 space-y-3">
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <button type="button" className="text-sm underline" onClick={() => void load()}>
            Retry product placements
          </button>
          <button type="button" className="ml-4 text-sm underline" onClick={onCancel}>
            {cancelLabel}
          </button>
        </div>
      )}
    </div>
  );
}

export function ProductPlacementPreview({
  allowedFormats,
  initialFormat,
  destination,
  load,
}: {
  allowedFormats: AdFormat[];
  initialFormat: AdFormat;
  destination?: "product_url" | "listing_url";
  load: (signal: AbortSignal) => Promise<CreativePreviewProduct | null>;
}) {
  const formats = automaticAdFormats.filter((format) => allowedFormats.includes(format));
  const [format, setFormat] = useState<AdFormat>(
    formats.includes(initialFormat) ? initialFormat : (formats[0] ?? "feed"),
  );
  const [product, setProduct] = useState<CreativePreviewProduct | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    setProduct(null);
    void load(controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setProduct(next);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load, revision]);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Appearance preview for this product using its saved destination. Unsaved placement changes
        do not affect preview availability, and a preview does not confirm delivery eligibility.
      </p>
      {formats.length > 1 && (
        <Select
          label="Product preview format"
          required
          value={format}
          onChange={(value) => setFormat(value as AdFormat)}
          options={formats.map((value) => ({ value, label: adFormatLabel(value) }))}
        />
      )}
      {loading || error || product ? (
        <AdCreativePreview
          format={format}
          product={product}
          destination={destination}
          loading={loading}
          error={error}
          onRetry={() => setRevision((current) => current + 1)}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          This product is unavailable for preview. It must be accepted, owned by this account, and
          allowed by saved product settings.
        </p>
      )}
    </div>
  );
}

export function parseCreativePreview(value: unknown): CreativePreviewProduct | null {
  if (!value || typeof value !== "object" || !("product" in value))
    throw new Error("Invalid creative preview.");
  if (value.product === null) return null;
  const product = value.product as Record<string, unknown>;
  if (
    !product ||
    typeof product !== "object" ||
    !["id", "name", "description", "product_url"].every(
      (key) => typeof product[key] === "string",
    ) ||
    !["logo_url", "image_url", "listing_url"].every(
      (key) => product[key] === null || typeof product[key] === "string",
    )
  )
    throw new Error("Invalid creative preview.");
  return product as CreativePreviewProduct;
}

function safeImage(value?: string | null) {
  try {
    const url = new URL(value ?? "");
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}
function PreviewMedia({
  image,
  logo,
  name,
  compact = false,
}: {
  image?: string | null;
  logo?: string | null;
  name: string;
  compact?: boolean;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);
  const hero = !imageFailed && safeImage(image);
  const icon = !logoFailed && safeImage(logo);
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
  return (
    <div
      className={
        compact
          ? "flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-muted"
          : "flex aspect-video items-center justify-center overflow-hidden bg-muted"
      }
    >
      {hero && !compact ? (
        <img
          src={hero}
          alt=""
          className="size-full object-cover"
          onError={() => setImageFailed(true)}
          referrerPolicy="no-referrer"
        />
      ) : icon ? (
        <img
          src={icon}
          alt=""
          className={compact ? "size-9 object-contain" : "size-20 object-contain"}
          onError={() => setLogoFailed(true)}
          referrerPolicy="no-referrer"
        />
      ) : (
        <span
          className={compact ? "font-semibold" : "text-3xl font-semibold text-muted-foreground"}
        >
          {initials}
        </span>
      )}
    </div>
  );
}

export function AdCreativePreview({
  format,
  product,
  article,
  destination = "product_url",
  loading,
  error,
  onRetry,
}: {
  format: AdFormat;
  product?: CreativePreviewProduct | null;
  article?: CreativePreviewArticle | null;
  destination?: "product_url" | "listing_url";
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
}) {
  const content = format === "sponsored_content";
  const title = content ? article?.title : product?.name;
  const description = content ? article?.summary : product?.description;
  const url = content && article ? `/articles/${article.slug}` : product?.[destination];
  let host = "";
  try {
    host = content ? (url ?? "") : new URL(url ?? "").hostname.replace(/^www\./, "");
  } catch {
    /* A destination may be unavailable. */
  }
  const disclosure = (
    <span className="inline-flex rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
      Sponsored
    </span>
  );
  return (
    <section aria-label="Creative preview" aria-busy={loading} className="min-w-0 space-y-3">
      <h3 className="text-sm font-medium">{adFormatLabel(format)}</h3>
      {loading ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading creative preview…
        </p>
      ) : error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>Could not load the creative preview.</p>
          {onRetry && (
            <button type="button" className="underline" onClick={onRetry}>
              Retry creative preview
            </button>
          )}
        </div>
      ) : title ? (
        <article className="max-w-md overflow-hidden rounded-xl border bg-card text-card-foreground">
          {format === "homepage_sponsorship" ? (
            <div className="flex items-center gap-3 p-4">
              <PreviewMedia key={product?.id} logo={product?.logo_url} name={title} compact />
              <div className="min-w-0 flex-1">
                {disclosure}
                <p className="mt-2 truncate font-semibold">{title}</p>
                {host && <p className="truncate text-xs text-muted-foreground">{host}</p>}
              </div>
            </div>
          ) : format === "engagement" || (format === "feed" && !safeImage(product?.image_url)) ? (
            <div className="space-y-3 p-4">
              <div className="flex items-start gap-3">
                <PreviewMedia key={product?.id} logo={product?.logo_url} name={title} compact />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="line-clamp-2 text-base leading-snug font-semibold">{title}</h4>
                    {format === "engagement" && disclosure}
                  </div>
                  {format === "engagement" && description && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{description}</p>
                  )}
                  {format === "engagement" && (
                    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                      <span className="truncate">{host}</span>
                      <span className="shrink-0">Explore ↗</span>
                    </div>
                  )}
                </div>
              </div>
              {format === "feed" && (
                <>
                  {description && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{description}</p>
                  )}
                  {disclosure}
                  <div className="border-t pt-3 text-xs text-muted-foreground">
                    <span className="block truncate">{host}</span>
                  </div>
                </>
              )}
            </div>
          ) : (
            <>
              <PreviewMedia
                key={`${product?.id ?? article?.id}/${product?.image_url ?? article?.image_url}`}
                image={content ? article?.image_url : product?.image_url}
                logo={product?.logo_url}
                name={title}
              />
              <div className="space-y-3 p-4">
                {disclosure}
                <h4 className="line-clamp-2 text-lg leading-snug font-semibold">{title}</h4>
                {description && (
                  <p className="line-clamp-3 text-sm text-muted-foreground">{description}</p>
                )}
                <div className="border-t pt-3 text-xs">
                  <span className="block truncate text-muted-foreground">{host}</span>
                </div>
              </div>
            </>
          )}
        </article>
      ) : (
        <p className="text-sm text-muted-foreground">
          {content
            ? "Choose an approved article to preview sponsored content."
            : "A preview will appear when this account has an accepted product."}
        </p>
      )}
      {title && !content && !url && (
        <p className="text-sm text-muted-foreground">
          This product has no available partner listing and cannot use that destination.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Example creative. Delivery follows the campaign’s targeting and eligible placement context.
      </p>
    </section>
  );
}
