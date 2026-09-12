import "server-only";
import { createHash } from "node:crypto";
import { cleanBody, markdownResponse, toMarkdownPath } from "@dualmark/core";
import { aiRoute } from "@/lib/ai-routes";
import { CATALOG_PAGE_SIZE, MAX_CATALOG_OFFSET, catalogPage } from "@/lib/catalog-page";
import {
  getArticle,
  getFeed,
  getSearch,
  getSource,
  getSources,
  getTag,
  getTags,
  getTopic,
  getTopics,
  UserApiError,
} from "@/lib/api";
import { feedHref, parseFilters, safeExternalUrl, sourceHref } from "@/lib/feed-query";
import { searchKinds } from "@/lib/search";
import { canonicalUrl, catalogCanonical, feedCanonical } from "@/lib/metadata";
import type { Article, Source } from "@/lib/types";
import { publicSiteOrigin } from "./config";

export function label(value: string) {
  return cleanBody(value)
    .replace(/\s+/g, " ")
    .replace(/[\\`*_[\]<>]/g, "\\$&");
}
export function publicLink(title: string, path: string) {
  const url = new URL(path, publicSiteOrigin());
  if (url.origin !== publicSiteOrigin()) throw new Error("Non-public content link");
  url.pathname = toMarkdownPath(url.pathname);
  return `[${label(title)}](<${url.href}>)`;
}
function externalLink(title: string, href: string | null) {
  const safe = safeExternalUrl(href);
  return safe ? `[${label(title)}](<${safe.replace(/>/g, "%3E").replace(/</g, "%3C")}>)` : "";
}
export function articleMarkdown(article: Article, level = 1) {
  const overview = article.ai_summary || article.summary;
  return [
    `${"#".repeat(level)} ${label(article.title)}`,
    `DevFeed: ${publicLink(article.title, `/articles/${encodeURIComponent(article.slug)}`)}`,
    `Original publisher: ${externalLink("Read original article", article.canonical_url)}`,
    article.author && `Author: ${label(article.author)}`,
    `Published: ${label(article.published_at ?? article.feed_at)}`,
    `Content type: ${label(article.content_type)}`,
    article.language && `Language: ${label(article.language)}`,
    article.sources.length > 0 &&
      `Sources: ${article.sources.map((s) => publicLink(s.name, sourceHref(s))).join(", ")}`,
    article.topics.length > 0 &&
      `Topics: ${article.topics.map((t) => publicLink(t.name, `/topics/${encodeURIComponent(t.slug)}`)).join(", ")}`,
    article.tags.length > 0 &&
      `Tags: ${article.tags.map((t) => publicLink(t, `/tags/${encodeURIComponent(t)}`)).join(", ")}`,
    overview &&
      `${"#".repeat(level + 1)} ${article.ai_summary ? "AI overview" : "Overview"}\n\n${cleanBody(overview)}`,
    article.ai_summary &&
      article.summary &&
      article.summary.trim() !== article.ai_summary.trim() &&
      `${"#".repeat(level + 1)} Source excerpt\n\n${cleanBody(article.summary)}`,
  ]
    .filter(Boolean)
    .join("\n\n");
}

function offsetValue(query: URLSearchParams) {
  const value = query.get("offset") ?? "0";
  if (!/^\d{1,7}$/.test(value) || Number(value) > MAX_CATALOG_OFFSET) throw new UserApiError(400);
  return Number(value);
}
function nextLink(path: string, query: URLSearchParams, key: string, value: string) {
  const next = new URLSearchParams(query);
  next.set(key, value);
  return publicLink("Next page", `${path}?${next}`);
}

/** Fetches only public, already cache-enabled APIs. Never forwards cookies or authorization. */
export async function renderPublicMarkdown(
  path: string,
  query = new URLSearchParams(),
  resolvedSource?: Source,
): Promise<string> {
  const route = aiRoute(path);
  if (!route) throw new UserApiError(404);
  if (route.kind === "article") return articleMarkdown(await getArticle(route.slug));
  if (route.kind === "directory") {
    const offset = offsetValue(query);
    const limit = CATALOG_PAGE_SIZE;
    const items =
      route.collection === "topics"
        ? await getTopics(offset, limit)
        : route.collection === "sources"
          ? await getSources(offset, limit)
          : await getTags(offset, limit);
    const links = items.map((item) => {
      const id = item.slug;
      const description = "description" in item ? item.description : null;
      return `- ${publicLink(item.name, `/${route.collection}/${encodeURIComponent(id)}`)}${typeof description === "string" && description ? `: ${label(description)}` : ""}`;
    });
    const next = catalogPage<{ id: string }>(items, offset).next_cursor;
    return [
      `# DevFeed ${route.collection}`,
      "Public entries with published articles. This directory is paginated.",
      ...links,
      !items.length && "No more entries.",
      next && nextLink(path, new URLSearchParams(), "offset", next),
    ]
      .filter(Boolean)
      .join("\n\n");
  }
  if (route.kind === "search") {
    const q = (query.get("q") ?? "").trim().slice(0, 200);
    if (!q)
      return "# Search DevFeed\n\nUse /search.md?q=your+query to search articles, topics, sources and tags. Articles appear first.";
    const section = query.get("section") ?? undefined;
    const page = query.get("page") ?? "1";
    if ((section && !searchKinds.some((kind) => kind === section)) || !/^[1-9]\d{0,3}$/.test(page))
      throw new UserApiError(400);
    const result = await getSearch(q, section, page);
    return [
      `# Search: ${label(result.query)}`,
      ...searchKinds.flatMap((kind) => {
        const group = result.sections[kind];
        if (!group) return [];
        return [
          `## ${kind}`,
          ...group.items.map(
            (hit) =>
              `- ${publicLink(hit.title, hit.href)}${hit.description ? `: ${label(hit.description)}` : ""}`,
          ),
          !group.items.length && "No matches.",
          group.next_cursor &&
            publicLink(
              `More ${kind}`,
              `/search?${new URLSearchParams({ q, section: kind, page: group.next_cursor })}`,
            ),
        ].filter(Boolean);
      }),
    ].join("\n\n");
  }
  const filters = parseFilters(Object.fromEntries(query));
  if (route.contentType) filters.content_type = route.contentType;
  let title = route.contentType ? `DevFeed ${route.contentType}` : "DevFeed — Developer news";
  let description =
    "Published developer news, articles, tutorials, releases, comparisons and opinions.";
  if (route.collection && route.id) {
    const item =
      route.collection === "topics"
        ? await getTopic(route.id)
        : route.collection === "sources"
          ? (resolvedSource ?? (await getSource(route.id)))
          : await getTag(route.id);
    title = item.name;
    description =
      ("description" in item && typeof item.description === "string" ? item.description : null) ||
      `Published articles for ${item.name}.`;
    if (route.collection === "topics") filters.topic = route.id;
    if (route.collection === "sources") {
      filters.source_id = item.id;
      filters.source_slug = item.slug;
    }
    if (route.collection === "tags") filters.tag = route.id;
  }
  // An explicit empty cookie bypasses the reader's per-account content preferences.
  const feed = await getFeed(filters, undefined, "");
  return [
    `# ${label(title)}`,
    label(description),
    "This is one page of public article previews, not the complete archive. Follow Next page to continue. Summaries are not the original full articles.",
    ...feed.items.map((article) => articleMarkdown(article, 2)),
    !feed.items.length && "No published articles on this page.",
    feed.next_cursor && publicLink("Next page", feedHref(filters, { cursor: feed.next_cursor })),
  ]
    .filter(Boolean)
    .join("\n\n");
}

export function conditionalResponse(request: Request, response: Response, body: string) {
  const etag = `"${createHash("sha256").update(body).digest("hex")}"`;
  response.headers.set("ETag", etag);
  const matches = request.headers
    .get("if-none-match")
    ?.split(",")
    .some((value) => value.trim().replace(/^W\//, "") === etag || value.trim() === "*");
  return matches ? new Response(null, { status: 304, headers: response.headers }) : response;
}
export function aiUnavailable(error: unknown) {
  const status =
    error instanceof UserApiError && [400, 404, 422].includes(error.status) ? error.status : 503;
  return new Response(
    status === 404
      ? "Public page not found"
      : status === 503
        ? "Public content temporarily unavailable"
        : "Invalid page parameters",
    {
      status,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex",
        ...(status === 503 ? { "Retry-After": "5" } : {}),
      },
    },
  );
}
export async function publicMarkdown(request: Request, parts: string[]) {
  // Next decodes params; encode once before using the shared route parser.
  const path = "/" + parts.map(encodeURIComponent).join("/");
  try {
    const query = new URL(request.url).searchParams;
    const route = aiRoute(path);
    const params = Object.fromEntries(query);
    const source =
      route?.kind === "feed" && route.collection === "sources" && route.id
        ? await getSource(route.id)
        : undefined;
    if (source && route?.kind === "feed" && route.id !== source.slug) {
      const target = path.replace(`/sources/${encodeURIComponent(route.id!)}`, sourceHref(source));
      return new Response(null, {
        status: 308,
        headers: {
          Location: canonicalUrl(target + ".md" + (query.size ? `?${query}` : "")),
          "Cache-Control": "no-store",
        },
      });
    }
    let body: string;
    let canonical: string;
    if (route?.kind === "article") {
      const article = await getArticle(route.slug);
      body = articleMarkdown(article);
      canonical = canonicalUrl(`/articles/${encodeURIComponent(article.slug)}`);
    } else {
      body = await renderPublicMarkdown(path, query, source);
      if (route?.kind === "feed") {
        canonical = feedCanonical(params, {
          ...(route.contentType ? { content_type: route.contentType } : {}),
          ...(route.collection === "topics" ? { topic: route.id } : {}),
          ...(route.collection === "tags" ? { tag: route.id } : {}),
          ...(source ? { source_id: source.id, source_slug: source.slug } : {}),
        });
      } else if (route?.kind === "directory") {
        canonical = catalogCanonical(path === "/tags" ? "/tags.md" : path, params);
      } else {
        canonical = canonicalUrl(path + (query.size ? `?${query}` : ""));
      }
    }
    const response = markdownResponse(body, {
      // Revalidate through the shared backend cache so moderation invalidations are honored.
      cacheControl: "public, max-age=0, must-revalidate",
      extraHeaders: { Vary: "Accept, User-Agent", Link: `<${canonical}>; rel="canonical"` },
    });
    return conditionalResponse(request, response, body);
  } catch (error) {
    return aiUnavailable(error);
  }
}
