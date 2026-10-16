import type { Article } from "./types";
import type { SearchKind } from "./search";
import { safeExternalUrl } from "./feed-query";

export const adFormats = ["feed", "header_sponsorship", "sponsored_content", "engagement"] as const;
export type AdFormat = (typeof adFormats)[number];
export const SEARCH_PLACEMENT_RESULT_LIMIT = 120;
export type SearchPlacementContext = {
  query: string;
  results: Partial<Record<SearchKind, string[]>>;
};
export type PlacementQuery = {
  surface: "article" | "topic" | "feed" | "search" | "header";
  article_id?: string;
  topic_id?: string;
  article_ids?: string[];
  search?: SearchPlacementContext;
  allow_sponsored_fallback?: boolean;
  exclude_product_ids?: string[];
  ad_formats?: AdFormat[];
};

export type PartnerPlacement = {
  product_id: string;
  asset_id: string;
  campaign_id: string | null;
  name: string;
  tagline: string;
  logo_url: string | null;
  image_url: string | null;
  image_variants: { url: string; width: number }[];
  url: string;
  partner_name: string;
  platform: {
    name: string;
    logo_url: string | null;
    logo_variants: { url: string; width: number }[];
  } | null;
  sponsored: boolean;
  reason: string;
  receipt: string;
  ad_format: AdFormat;
  article: Article | null;
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function validSearchContext(value: unknown): value is SearchPlacementContext {
  if (
    !record(value) ||
    Object.keys(value).some((key) => !["query", "results"].includes(key)) ||
    typeof value.query !== "string" ||
    value.query.length > 200 ||
    value.query.includes("\0") ||
    !value.query.isWellFormed() ||
    !record(value.results)
  )
    return false;
  const query = value.query.normalize("NFKC").trim().replace(/\s+/g, " ");
  if (!/[\p{L}\p{N}]/u.test(query) || query.length > 200 || query.split(" ").length > 20)
    return false;
  let count = 0;
  for (const [kind, ids] of Object.entries(value.results)) {
    if (
      !["articles", "topics", "sources", "tags"].includes(kind) ||
      !Array.isArray(ids) ||
      new Set(ids).size !== ids.length ||
      !ids.every((id) => typeof id === "string" && uuid.test(id))
    )
      return false;
    count += ids.length;
  }
  return count > 0 && count <= SEARCH_PLACEMENT_RESULT_LIMIT;
}

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
          "search",
          "allow_sponsored_fallback",
          "exclude_product_ids",
          "ad_formats",
        ].includes(key),
    )
  )
    return false;
  if (
    value.search !== undefined &&
    (value.surface !== "search" ||
      value.article_ids !== undefined ||
      !validSearchContext(value.search))
  )
    return false;
  if (
    value.ad_formats !== undefined &&
    (!Array.isArray(value.ad_formats) ||
      value.ad_formats.length < 1 ||
      value.ad_formats.length > 4 ||
      new Set(value.ad_formats).size !== value.ad_formats.length ||
      !value.ad_formats.every((format) => adFormats.includes(format as AdFormat)) ||
      (value.surface !== "feed" && value.ad_formats.includes("sponsored_content")) ||
      (value.surface !== "header" && value.ad_formats.includes("header_sponsorship")))
  )
    return false;
  if (
    (value.surface === "search" || value.surface === "header") &&
    (!Array.isArray(value.ad_formats) ||
      value.ad_formats.length !== 1 ||
      value.ad_formats[0] !== (value.surface === "header" ? "header_sponsorship" : "engagement"))
  )
    return false;
  if (
    value.allow_sponsored_fallback !== undefined &&
    (typeof value.allow_sponsored_fallback !== "boolean" ||
      (value.surface !== "feed" && value.allow_sponsored_fallback))
  )
    return false;
  if (
    value.article_ids !== undefined &&
    (!["feed", "search", "header"].includes(String(value.surface)) ||
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
  if (value.surface === "header")
    return (
      (value.article_id === undefined ||
        (typeof value.article_id === "string" && uuid.test(value.article_id))) &&
      (value.topic_id === undefined ||
        (typeof value.topic_id === "string" && uuid.test(value.topic_id))) &&
      [
        value.article_id !== undefined,
        value.topic_id !== undefined,
        Array.isArray(value.article_ids) && value.article_ids.length > 0,
      ].filter(Boolean).length <= 1
    );
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
  return (
    (value.surface === "feed" || value.surface === "search") &&
    value.article_id === undefined &&
    value.topic_id === undefined
  );
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
      !text(item.tagline, 160) ||
      item.tagline.trim().length < 3 ||
      !text(item.partner_name, 200) ||
      !text(item.reason, 1000) ||
      !text(item.receipt, 256) ||
      !text(item.url, 2048)
    )
      throw new Error("Invalid partner placement");
    const format = item.ad_format ?? "feed";
    if (!adFormats.includes(format as AdFormat))
      throw new Error("Invalid partner placement format");
    const article = format === "sponsored_content" ? parseSponsoredArticle(item.article) : null;
    const url = new URL(item.url, "https://devfeed.tech");
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (article ? item.url !== `/articles/${article.slug}` : !item.url.startsWith("https://")) ||
      products.has(String(item.product_id))
    )
      throw new Error("Invalid partner placement destination");
    products.add(String(item.product_id));
    return {
      product_id: String(item.product_id),
      asset_id: String(item.asset_id),
      campaign_id: item.campaign_id,
      name: item.name,
      tagline: item.tagline,
      logo_url: safeLogoUrl(item.logo_url),
      image_url: safeLogoUrl(item.image_url),
      image_variants: safeImageVariants(item.image_variants),
      url: item.url,
      partner_name: item.partner_name,
      platform: parsePlatform(item.platform),
      sponsored: item.sponsored,
      reason: item.reason,
      receipt: item.receipt,
      ad_format: format as AdFormat,
      article,
    };
  });
}

function parsePlatform(value: unknown): PartnerPlacement["platform"] {
  if (value === null) return null;
  if (!record(value) || !text(value.name, 200))
    throw new Error("Invalid partner placement platform");
  return {
    name: value.name,
    logo_url: safeLogoUrl(value.logo_url),
    logo_variants: safeImageVariants(value.logo_variants),
  };
}

/** Project approved editorial content, never operational fields, into the reader model. */
function parseSponsoredArticle(value: unknown): Article {
  const slug = (input: unknown): input is string =>
    text(input, 300) && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(input);
  const nullable = (input: unknown, limit = Infinity) =>
    input === null || text(input, limit, false);
  if (
    !record(value) ||
    !text(value.id, 36) ||
    !uuid.test(value.id) ||
    !slug(value.slug) ||
    !text(value.canonical_url, 2048) ||
    !safeExternalUrl(value.canonical_url) ||
    !text(value.title, 1000) ||
    typeof value.summary !== "string" ||
    !nullable(value.ai_summary) ||
    !nullable(value.ai_description) ||
    !nullable(value.author, 1000) ||
    !text(value.content_type, 100) ||
    !nullable(value.content_format, 100) ||
    !nullable(value.language, 100) ||
    !nullable(value.published_at, 100) ||
    !text(value.feed_at, 100) ||
    !Array.isArray(value.tags) ||
    value.tags.length > 100 ||
    !value.tags.every((tag) => text(tag, 200)) ||
    !Array.isArray(value.sources) ||
    value.sources.length > 100 ||
    !Array.isArray(value.topics) ||
    value.topics.length > 100
  )
    throw new Error("Invalid sponsored article");
  const sources: Article["sources"] = value.sources.map((source: unknown) => {
    if (
      !record(source) ||
      !text(source.id, 36) ||
      !uuid.test(source.id) ||
      !slug(source.slug) ||
      !text(source.name, 200)
    )
      throw new Error("Invalid sponsored article source");
    return {
      id: source.id,
      slug: source.slug,
      name: source.name,
      website_url: safeLogoUrl(source.website_url),
      logo_url: safeLogoUrl(source.logo_url),
      logo_variants: safeImageVariants(source.logo_variants),
      description: typeof source.description === "string" ? source.description : null,
    };
  });
  const topics: Article["topics"] = value.topics.map((topic: unknown) => {
    if (
      !record(topic) ||
      !text(topic.id, 36) ||
      !uuid.test(topic.id) ||
      !slug(topic.slug) ||
      !text(topic.name, 200) ||
      !text(topic.kind, 100)
    )
      throw new Error("Invalid sponsored article topic");
    return {
      id: topic.id,
      slug: topic.slug,
      name: topic.name,
      kind: topic.kind,
      ...(text(topic.role, 100) ? { role: topic.role } : {}),
    };
  });
  return {
    id: value.id,
    slug: value.slug,
    canonical_url: String(value.canonical_url),
    title: value.title,
    summary: value.summary,
    ai_summary: value.ai_summary as string | null,
    ai_description: value.ai_description as string | null,
    image_url: safeLogoUrl(value.image_url),
    image_variants: safeImageVariants(value.image_variants),
    author: value.author as string | null,
    content_type: value.content_type,
    content_format: value.content_format as string | null,
    language: value.language as string | null,
    published_at: value.published_at as string | null,
    feed_at: value.feed_at,
    tags: value.tags as string[],
    sources,
    topics,
    sponsor_names: Array.isArray(value.sponsor_names)
      ? value.sponsor_names.filter((name): name is string => text(name, 200)).slice(0, 20)
      : [],
  };
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
