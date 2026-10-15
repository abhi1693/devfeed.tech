"use client";

/* eslint-disable @next/next/no-img-element -- Previews use managed media and the owner's draft image. */
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Dialog } from "radix-ui";
import { Upload } from "lucide-react";
import { Select } from "./select";
import { Input } from "./input";
import { Button } from "./button";
import { useDebouncedImagePreview } from "./use-debounced-image-preview";
import { ConnectionLogo, type ConnectionLogoVariant } from "./connection-logo";

export const adFormats = [
  {
    value: "feed",
    label: "Feed",
    description: "Native product cards in grid feeds and sponsored rows in compact feed tables.",
  },
  {
    value: "header_sponsorship",
    label: "Header sponsorship",
    description:
      "A compact sponsor placement in the reader’s top navigation across pages on wide screens.",
  },
  {
    value: "sponsored_content",
    label: "Sponsored content",
    description: "An approved sponsored article in relevant grid feeds, opened inside the reader.",
  },
  {
    value: "engagement",
    label: "Engagement",
    description: "Compact product recommendations in topic, source, search and article sidebars.",
  },
] as const;
export type AdFormat = (typeof adFormats)[number]["value"];
export const automaticAdFormats: AdFormat[] = ["feed", "header_sponsorship", "engagement"];
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
  tagline: string;
  logo_url: string | null;
  image_url: string | null;
  product_url: string;
  listing_url: string | null;
  platform?: {
    name: string;
    logo_url: string | null;
    logo_variants?: ConnectionLogoVariant[];
  } | null;
  ad_image_status?: ProductAdImageStatus;
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
  { value: "header", label: "Header" },
  { value: "article", label: "Article pages" },
  { value: "topic", label: "Topic and category pages" },
  { value: "feed", label: "Reader feeds" },
  { value: "search", label: "Search results" },
] as const;
export type PlacementSurface = (typeof placementSurfaces)[number]["value"];
export type ProductPlacementRestrictions = {
  ad_formats: AdFormat[] | null;
  promotion_surfaces: PlacementSurface[] | null;
};
export type ProductAdImageStatus = "default" | "queued" | "running" | "ready" | "failed";
function validAdImageStatus(value: unknown): value is ProductAdImageStatus {
  return (
    typeof value === "string" && ["default", "queued", "running", "ready", "failed"].includes(value)
  );
}
export type ProductPlacementPolicy = ProductPlacementRestrictions & {
  excluded: boolean;
  ad_image_url: string | null;
  image_url?: string | null;
  default_image_url?: string | null;
  ad_image_status?: ProductAdImageStatus;
};
function sameSelection(left: string[] | null, right: string[] | null) {
  return left === null || right === null
    ? left === right
    : left.length === right.length && left.every((item) => right.includes(item));
}
function sameProductPolicy(left: ProductPlacementPolicy, right: ProductPlacementPolicy) {
  return (
    left.excluded === right.excluded &&
    left.ad_image_url === right.ad_image_url &&
    sameSelection(left.ad_formats, right.ad_formats) &&
    sameSelection(left.promotion_surfaces, right.promotion_surfaces)
  );
}
export function parseProductPlacementPolicy(value: unknown): ProductPlacementPolicy {
  if (!value || typeof value !== "object") throw new Error("Invalid product placement settings.");
  const row = value as ProductPlacementPolicy;
  if (
    typeof row.excluded !== "boolean" ||
    !(row.ad_image_url === null || typeof row.ad_image_url === "string") ||
    (typeof row.ad_image_url === "string" && !normalizeAdImageUrl(row.ad_image_url)) ||
    (row.ad_image_status !== undefined && !validAdImageStatus(row.ad_image_status)) ||
    ["image_url", "default_image_url"].some(
      (key) =>
        key in row &&
        row[key as "image_url" | "default_image_url"] !== null &&
        typeof row[key as "image_url" | "default_image_url"] !== "string",
    ) ||
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
  uploadImage?: (file: File, signal: AbortSignal) => Promise<ProductPlacementPolicy>;
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
  uploadImage,
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
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [uploadedImage, setUploadedImage] = useState<ProductPlacementPolicy | null>(null);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<ProductPlacementPolicy | null>(null);
  const [excluded, setExcluded] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(previewInline);
  const [formatMode, setFormatMode] = useState("inherit");
  const [surfaceMode, setSurfaceMode] = useState("inherit");
  const imageControlId = useId();
  const [imageMode, setImageMode] = useState("default");
  const [imageUrl, setImageUrl] = useState("");
  const [formats, setFormats] = useState<AdFormat[]>([]);
  const [surfaces, setSurfaces] = useState<PlacementSurface[]>([]);
  const request = useRef<AbortController | null>(null);
  const uploadRequest = useRef<AbortController | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);
  // The caller keys this editor by account and product. Parent refreshes must
  // preserve an open draft even when they create new callback/format arrays.
  const initial = useRef({ loadPolicy, allowedFormats, previewInline });
  useEffect(
    () => () => {
      request.current?.abort();
      uploadRequest.current?.abort();
    },
    [],
  );
  const busy = saving || uploading;
  const draft: ProductPlacementPolicy = {
    excluded,
    ad_formats: formatMode === "inherit" ? null : formats,
    promotion_surfaces: surfaceMode === "inherit" ? null : surfaces,
    ad_image_url: imageMode === "custom" ? normalizeAdImageUrl(imageUrl) : null,
  };
  const dirty =
    policy &&
    (!sameProductPolicy(policy, draft) || (imageMode === "custom" && !draft.ad_image_url));
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
      setImageMode(next.ad_image_url ? "custom" : "default");
      setImageUrl(next.ad_image_url ?? "");
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
    if (busy || !policy) return;
    if (imageMode === "custom" && !draft.ad_image_url) {
      setError("Enter a public HTTPS URL for the ad image.");
      return;
    }
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
        setUploadedImage(null);
        setUploadMessage("");
        onSaved?.();
      }
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Could not save product placements.");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  async function upload(file: File) {
    if (busy || uploadRequest.current || !policy || !uploadImage) return;
    setError("");
    setUploadMessage("");
    if (!file.size || file.size > 5 * 1024 * 1024) {
      setError("Choose an image under 5 MB.");
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("Choose a JPEG, PNG or WebP image.");
      return;
    }
    const controller = new AbortController();
    uploadRequest.current = controller;
    setUploading(true);
    try {
      const next = parseProductPlacementPolicy(await uploadImage(file, controller.signal));
      if (controller.signal.aborted) return;
      if (
        !next.ad_image_url ||
        next.image_url !== next.ad_image_url ||
        next.ad_image_status !== "ready"
      )
        throw new Error("Could not confirm the uploaded ad image. Refresh and try again.");
      setPolicy(
        (current) =>
          current && {
            ...current,
            ad_image_url: next.ad_image_url,
            image_url: next.image_url,
            default_image_url: next.default_image_url,
            ad_image_status: next.ad_image_status,
          },
      );
      setImageMode("custom");
      setImageUrl(next.ad_image_url);
      setUploadedImage(next);
      setPreviewOpen(true);
      setUploadMessage("Image uploaded and applied. Save any other placement changes separately.");
    } catch (reason) {
      if (!controller.signal.aborted)
        setError(
          reason instanceof Error ? reason.message : "Could not upload the ad image. Try again.",
        );
    } finally {
      if (uploadRequest.current === controller) uploadRequest.current = null;
      if (!controller.signal.aborted) setUploading(false);
    }
  }
  function cancel() {
    uploadRequest.current?.abort();
    uploadRequest.current = null;
    setUploading(false);
    onCancel();
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
            aria-busy={busy}
            className={
              previewInline ? "mt-5 grid items-start gap-6 lg:grid-cols-2" : "mt-5 space-y-5"
            }
          >
            <div className="space-y-5">
              <Select
                label="Product availability"
                required
                value={excluded ? "excluded" : "allowed"}
                disabled={busy}
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
                disabled={busy}
                onChange={setFormatMode}
                options={[
                  { value: "inherit", label: "Inherit account formats" },
                  { value: "custom", label: "Choose product formats" },
                ]}
              />
              {formatMode === "custom" && (
                <fieldset disabled={busy} className="space-y-2">
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
                disabled={busy}
                onChange={setSurfaceMode}
                options={[
                  { value: "inherit", label: "Inherit account placements" },
                  { value: "custom", label: "Choose product placements" },
                ]}
              />
              {surfaceMode === "custom" && (
                <fieldset disabled={busy} className="space-y-2">
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
              <div className="space-y-3 border-t pt-4">
                <label htmlFor={`${imageControlId}-mode`} className="text-sm font-medium">
                  Ad image
                </label>
                <Select
                  id={`${imageControlId}-mode`}
                  label="Ad image"
                  value={imageMode}
                  onChange={setImageMode}
                  disabled={busy}
                  required
                  options={[
                    { value: "default", label: "Website image" },
                    { value: "custom", label: "Custom image" },
                  ]}
                />
                {imageMode === "custom" && (
                  <div className="space-y-2">
                    <label htmlFor={`${imageControlId}-url`} className="text-sm font-medium">
                      Ad image URL
                    </label>
                    <Input
                      id={`${imageControlId}-url`}
                      type="url"
                      value={imageUrl}
                      onChange={(event) => setImageUrl(event.target.value)}
                      placeholder="https://cdn.example.com/product-ad.jpg"
                      maxLength={2048}
                      disabled={busy}
                      required
                      aria-describedby={`${imageControlId}-help`}
                    />
                  </div>
                )}
                {imageMode === "custom" && uploadImage && (
                  <div className="space-y-2">
                    <input
                      ref={uploadInput}
                      type="file"
                      className="sr-only"
                      aria-label="Upload ad image file"
                      accept="image/jpeg,image/png,image/webp"
                      disabled={busy}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) void upload(file);
                      }}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      aria-busy={uploading}
                      onClick={() => uploadInput.current?.click()}
                    >
                      <Upload className="size-4" aria-hidden="true" />
                      {uploading ? "Uploading…" : "Upload image"}
                    </Button>
                    <p className="text-xs text-muted-foreground">
                      JPEG, PNG or WebP, up to 5 MB. Uploading applies the image immediately.
                    </p>
                    {uploadMessage && (
                      <p role="status" className="text-sm">
                        {uploadMessage}
                      </p>
                    )}
                  </div>
                )}
                <p id={`${imageControlId}-help`} className="text-xs text-muted-foreground">
                  Save an image URL to prepare it for delivery; the website image stays in use until
                  it is ready. Article sponsorship keeps the article’s image.
                </p>
              </div>
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
                    imageUrlOverride={
                      draft.ad_image_url !== policy.ad_image_url
                        ? imageMode === "custom"
                          ? draftImagePreviewUrl(draft.ad_image_url)
                          : policy.default_image_url
                        : uploadedImage?.image_url
                    }
                    draftImage={draft.ad_image_url !== policy.ad_image_url}
                    savedImageStatus={uploadedImage?.ad_image_status}
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
                onClick={cancel}
              >
                {cancelLabel}
              </button>
              <button
                type="submit"
                disabled={busy}
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
  imageUrlOverride,
  draftImage = false,
  savedImageStatus,
}: {
  allowedFormats: AdFormat[];
  initialFormat: AdFormat;
  destination?: "product_url" | "listing_url";
  load: (signal: AbortSignal) => Promise<CreativePreviewProduct | null>;
  imageUrlOverride?: string | null;
  draftImage?: boolean;
  savedImageStatus?: ProductAdImageStatus;
}) {
  const formats = automaticAdFormats.filter((format) => allowedFormats.includes(format));
  const [format, setFormat] = useState<AdFormat>(
    formats.includes(initialFormat) ? initialFormat : (formats[0] ?? "feed"),
  );
  const [product, setProduct] = useState<CreativePreviewProduct | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [imagePolls, setImagePolls] = useState(0);
  const settledImage = useDebouncedImagePreview(imageUrlOverride ?? null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    setProduct(null);
    setImagePolls(0);
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
  const imageStatus = savedImageStatus ?? product?.ad_image_status;
  useEffect(() => {
    if (draftImage || imagePolls >= 20 || (imageStatus !== "queued" && imageStatus !== "running"))
      return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void load(controller.signal).then(
        (next) => {
          if (!controller.signal.aborted) {
            setProduct(next);
            setImagePolls((count) => count + 1);
          }
        },
        () => {
          if (!controller.signal.aborted) setImagePolls((count) => count + 1);
        },
      );
    }, 3000);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [draftImage, imageStatus, imagePolls, load, revision]);
  const visibleProduct =
    product && imageUrlOverride !== undefined
      ? { ...product, image_url: imageUrlOverride === null ? null : settledImage }
      : product;
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Appearance preview for this product using its saved destination. Unsaved placement changes
        do not affect preview availability, and a preview does not confirm delivery eligibility.
      </p>
      {draftImage ? (
        <p role="status" aria-label="Ad image status" className="text-xs text-muted-foreground">
          Unsaved image preview. Save product placements to apply it.
        </p>
      ) : imageStatus === "queued" || imageStatus === "running" ? (
        <p role="status" aria-label="Ad image status" className="text-xs text-muted-foreground">
          Preparing the custom image. The website image remains in use until it is ready.
        </p>
      ) : imageStatus === "failed" ? (
        <p role="status" aria-label="Ad image status" className="text-xs text-muted-foreground">
          The custom image could not be prepared. The website image remains in use. Save product
          placements to retry, or choose another URL.
        </p>
      ) : imageStatus === "ready" ? (
        <p role="status" aria-label="Ad image status" className="text-xs text-muted-foreground">
          Using the custom ad image.
        </p>
      ) : null}
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
          product={visibleProduct}
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
      <Button
        size="sm"
        variant="outline"
        disabled={loading}
        onClick={() => setRevision((value) => value + 1)}
      >
        Refresh preview
      </Button>
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
    !["id", "name", "tagline", "product_url"].every((key) => typeof product[key] === "string") ||
    (typeof product.tagline === "string" &&
      (product.tagline.trim().length < 3 || product.tagline.length > 160)) ||
    !["logo_url", "image_url", "listing_url"].every(
      (key) => product[key] === null || typeof product[key] === "string",
    ) ||
    (product.ad_image_status !== undefined && !validAdImageStatus(product.ad_image_status))
  )
    throw new Error("Invalid creative preview.");
  const platform = product.platform;
  if (
    platform !== undefined &&
    platform !== null &&
    (typeof platform !== "object" ||
      !("name" in platform) ||
      typeof platform.name !== "string" ||
      !platform.name.trim() ||
      platform.name.length > 200 ||
      !("logo_url" in platform) ||
      !(platform.logo_url === null || typeof platform.logo_url === "string") ||
      ("logo_variants" in platform &&
        platform.logo_variants !== undefined &&
        (!Array.isArray(platform.logo_variants) ||
          platform.logo_variants.length > 20 ||
          !platform.logo_variants.every(
            (variant) =>
              variant &&
              typeof variant === "object" &&
              typeof variant.url === "string" &&
              Number.isSafeInteger(variant.width) &&
              variant.width > 0,
          ))))
  )
    throw new Error("Invalid creative preview platform.");
  return product as CreativePreviewProduct;
}

function normalizeAdImageUrl(value: string) {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 2048 || /[\s\u0000-\u001f\u007f]/.test(trimmed)) return null;
  try {
    const url = new URL(trimmed);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (url.port && url.port !== "443")
    )
      return null;
    url.hash = "";
    url.hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    return url.href;
  } catch {
    return null;
  }
}

// An unsaved URL is only previewed in its owner's form. Delivery uses managed assets.
function draftImagePreviewUrl(value: string | null) {
  if (!value) return null;
  const url = new URL(value);
  const host = url.hostname;
  if (
    !host.includes(".") ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    /^\d+(?:\.\d+){3}$/.test(host)
  )
    return null;
  return value;
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
  variant = "hero",
}: {
  image?: string | null;
  logo?: string | null;
  name: string;
  variant?: "hero" | "compact" | "inline";
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);
  const hero = !imageFailed && safeImage(image);
  const icon = !logoFailed && safeImage(logo);
  const compact = variant !== "hero";
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
          ? `flex shrink-0 items-center justify-center overflow-hidden ${variant === "inline" ? "size-8" : "size-11 rounded-lg bg-muted"}`
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
          className={compact ? "size-8 object-contain" : "size-20 object-contain"}
          onError={() => setLogoFailed(true)}
          referrerPolicy="no-referrer"
        />
      ) : (
        <span
          className={
            compact
              ? `${variant === "inline" ? "text-[10px]" : ""} font-semibold`
              : "text-3xl font-semibold text-muted-foreground"
          }
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
  const copy = content ? article?.summary : product?.tagline;
  const url = content && article ? `/articles/${article.slug}` : product?.[destination];
  let host = "";
  try {
    host = content ? (url ?? "") : new URL(url ?? "").hostname.replace(/^www\./, "");
  } catch {
    /* A destination may be unavailable. */
  }
  const disclosure = (
    <span
      className={
        format === "header_sponsorship"
          ? "shrink-0 text-[11px] font-normal whitespace-nowrap text-muted-foreground"
          : "inline-flex shrink-0 rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-xs font-semibold whitespace-nowrap text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
      }
    >
      Sponsored
    </span>
  );
  const platform = product?.platform;
  const feedMetadata = (
    <div className="flex min-w-0 items-center justify-between gap-2">
      {platform && (
        <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
          <ConnectionLogo
            name={platform.name}
            url={platform.logo_url}
            variants={platform.logo_variants}
            size={16}
          />
          <span className="truncate" title={platform.name}>
            {platform.name}
          </span>
        </span>
      )}
      {disclosure}
    </div>
  );
  return (
    <section aria-label="Creative preview" aria-busy={loading} className="min-w-0 space-y-3">
      <h3 className="text-sm font-medium">{adFormatLabel(format)}</h3>
      <p className="text-xs text-muted-foreground">
        {adFormats.find((item) => item.value === format)?.description}
      </p>
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
        <article
          aria-label={format === "header_sponsorship" ? "Desktop navigation preview" : undefined}
          className={`${format === "header_sponsorship" ? "w-full" : "max-w-md"} overflow-hidden rounded-xl border bg-card text-card-foreground`}
        >
          {format === "header_sponsorship" ? (
            <div className="flex flex-wrap items-center gap-3 p-2 sm:p-3">
              <span aria-hidden className="text-sm font-semibold">
                DevFeed
              </span>
              <span
                aria-hidden
                className="rounded-md border bg-muted/50 px-2 py-1 text-xs text-muted-foreground"
              >
                Search
              </span>
              <div
                className="ml-auto flex min-w-0 max-w-full basis-[200px] grow items-center gap-2 pl-2 sm:max-w-[560px] sm:gap-3 sm:pl-7"
                title={host || undefined}
              >
                <PreviewMedia
                  key={product?.id}
                  logo={product?.logo_url}
                  name={title}
                  variant="inline"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-1 sm:gap-1.5">
                    <p className="min-w-0 truncate text-sm font-medium">{title}</p>
                    <span aria-hidden className="hidden shrink-0 text-muted-foreground sm:inline">
                      ·
                    </span>
                    {disclosure}
                  </div>
                  {copy && (
                    <p className="truncate text-xs leading-[1.45] text-muted-foreground">{copy}</p>
                  )}
                </div>
              </div>
              <span aria-hidden className="text-xs text-muted-foreground">
                Sign in
              </span>
            </div>
          ) : format === "engagement" || (format === "feed" && !safeImage(product?.image_url)) ? (
            <div className="space-y-3 p-4">
              <div className="flex items-start gap-3">
                <PreviewMedia
                  key={product?.id}
                  logo={product?.logo_url}
                  name={title}
                  variant="compact"
                />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="line-clamp-2 text-base leading-snug font-semibold">{title}</h4>
                    {format === "engagement" && disclosure}
                  </div>
                  {format === "engagement" && copy && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{copy}</p>
                  )}
                  {format === "engagement" && (
                    <p className="truncate text-xs text-muted-foreground">{host}</p>
                  )}
                </div>
              </div>
              {format === "feed" && (
                <>
                  {copy && <p className="line-clamp-2 text-xs text-muted-foreground">{copy}</p>}
                  {feedMetadata}
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
                {content && disclosure}
                <h4 className="line-clamp-2 text-lg leading-snug font-semibold">{title}</h4>
                {copy && <p className="line-clamp-3 text-sm text-muted-foreground">{copy}</p>}
                {!content && feedMetadata}
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
        Example creative. Delivery depends on targeting and eligibility.
      </p>
    </section>
  );
}
