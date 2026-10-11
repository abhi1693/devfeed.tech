export type PlacementQuery = {
  surface: "article" | "topic" | "feed";
  article_id?: string;
  topic_id?: string;
  article_ids?: string[];
  allow_sponsored_fallback?: boolean;
  exclude_product_ids?: string[];
};

export type PartnerPlacement = {
  product_id: string;
  asset_id: string;
  campaign_id: string | null;
  name: string;
  description: string;
  logo_url: string | null;
  image_url: string | null;
  image_variants: { url: string; width: number }[];
  url: string;
  partner_name: string;
  sponsored: boolean;
  reason: string;
  receipt: string;
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function validPlacementQuery(value: unknown): value is PlacementQuery {
  if (!record(value)) return false;
  if (
    Object.keys(value).some(
      (key) =>
        ![
          "surface",
          "article_id",
          "topic_id",
          "article_ids",
          "allow_sponsored_fallback",
          "exclude_product_ids",
        ].includes(key),
    )
  )
    return false;
  if (
    value.allow_sponsored_fallback !== undefined &&
    (value.surface !== "feed" || typeof value.allow_sponsored_fallback !== "boolean")
  )
    return false;
  if (
    value.article_ids !== undefined &&
    (value.surface !== "feed" ||
      !Array.isArray(value.article_ids) ||
      value.article_ids.length > 24 ||
      !value.article_ids.every((id) => typeof id === "string" && uuid.test(id)))
  )
    return false;
  if (
    value.exclude_product_ids !== undefined &&
    (!Array.isArray(value.exclude_product_ids) ||
      value.exclude_product_ids.length > 100 ||
      !value.exclude_product_ids.every((id) => typeof id === "string" && uuid.test(id)))
  )
    return false;
  if (value.surface === "article")
    return (
      typeof value.article_id === "string" &&
      uuid.test(value.article_id) &&
      value.topic_id === undefined
    );
  if (value.surface === "topic")
    return (
      typeof value.topic_id === "string" &&
      uuid.test(value.topic_id) &&
      value.article_id === undefined
    );
  return value.surface === "feed" && value.article_id === undefined && value.topic_id === undefined;
}

function text(value: unknown, limit: number, required = true): value is string {
  return typeof value === "string" && value.length <= limit && (!required || !!value.trim());
}

/** Validate the delivery boundary before rendering links or binding signed tracking receipts. */
export function parsePartnerPlacements(value: unknown): PartnerPlacement[] {
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 2)
    throw new Error("Invalid partner placements");
  const products = new Set<string>();
  return value.items.map((item: unknown) => {
    if (
      !record(item) ||
      ![item.product_id, item.asset_id].every((id) => typeof id === "string" && uuid.test(id)) ||
      !(
        item.campaign_id === null ||
        (typeof item.campaign_id === "string" && uuid.test(item.campaign_id))
      ) ||
      typeof item.sponsored !== "boolean" ||
      item.sponsored !== (item.campaign_id !== null) ||
      !text(item.name, 200) ||
      !text(item.description, 2000, false) ||
      !text(item.partner_name, 200) ||
      !text(item.reason, 1000) ||
      !text(item.receipt, 256) ||
      !text(item.url, 2048)
    )
      throw new Error("Invalid partner placement");
    const url = new URL(item.url);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      products.has(String(item.product_id))
    )
      throw new Error("Invalid partner placement destination");
    products.add(String(item.product_id));
    return {
      product_id: String(item.product_id),
      asset_id: String(item.asset_id),
      campaign_id: item.campaign_id,
      name: item.name,
      description: item.description,
      logo_url: safeLogoUrl(item.logo_url),
      image_url: safeLogoUrl(item.image_url),
      image_variants: safeImageVariants(item.image_variants),
      url: item.url,
      partner_name: item.partner_name,
      sponsored: item.sponsored,
      reason: item.reason,
      receipt: item.receipt,
    };
  });
}

function safeImageVariants(value: unknown): { url: string; width: number }[] {
  if (!Array.isArray(value) || value.length > 20) return [];
  const widths = new Set<number>();
  return value.flatMap((item) => {
    if (!record(item) || !Number.isSafeInteger(item.width) || Number(item.width) < 1) return [];
    const url = safeLogoUrl(item.url);
    const width = Number(item.width);
    if (!url || widths.has(width)) return [];
    widths.add(width);
    return [{ url, width }];
  });
}

function safeLogoUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}
