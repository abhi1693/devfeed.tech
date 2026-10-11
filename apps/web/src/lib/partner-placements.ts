export type PlacementQuery = {
  surface: "article" | "topic" | "feed";
  article_id?: string;
  topic_id?: string;
  exclude_product_ids?: string[];
};

export type PartnerPlacement = {
  product_id: string;
  asset_id: string;
  campaign_id: string | null;
  name: string;
  description: string;
  url: string;
  partner_name: string;
  sponsored: boolean;
  reason: string;
  receipt: string;
  frequency_cap: number;
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function validPlacementQuery(value: unknown): value is PlacementQuery {
  if (!record(value)) return false;
  if (
    Object.keys(value).some(
      (key) => !["surface", "article_id", "topic_id", "exclude_product_ids"].includes(key),
    )
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
      !text(item.url, 2048) ||
      !Number.isSafeInteger(item.frequency_cap) ||
      Number(item.frequency_cap) < 1
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
      url: item.url,
      partner_name: item.partner_name,
      sponsored: item.sponsored,
      reason: item.reason,
      receipt: item.receipt,
      frequency_cap: Number(item.frequency_cap),
    };
  });
}
