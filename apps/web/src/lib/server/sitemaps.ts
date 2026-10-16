import "server-only";
import { createHash } from "node:crypto";
import {
  readerDestinationContract,
  matchReaderDestinationPath,
  serializeReaderDestination,
  type ReaderDestinationDefinition,
} from "@devfeed/http/reader-destinations";
import { publicSiteOrigin } from "./config";
import { PublicApiClient, UserApiError } from "./api-client";

const inventoryPages = new Map(
  readerDestinationContract.routes.flatMap((definition) => {
    const policy = definition.sitemap;
    if (policy?.kind !== "inventory") return [];
    return [[policy.collection, { policy, definition }] as const];
  }),
);
export const sitemapKinds = [...inventoryPages.keys()];
const inventoryApi = new PublicApiClient({ deadlineMs: 55_000, upstreamCache: "fresh" });
const namespace = "http://www.sitemaps.org/schemas/sitemap/0.9";
export const sitemapMaxBytes = 52_428_800;
const maxLocations = 50_000;
// Next replaces this expression with the artifact's build timestamp in production.
const pageBuildTime = process.env.DEVFEED_WEB_BUILD_TIME ?? new Date().toISOString();
function publicationDate(value?: string) {
  if (!value) return pageBuildTime;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value))
    throw new Error("Invalid publication date");
  return new Date(value).toISOString();
}
type SitemapEntry = {
  loc: string;
  lastmod?: string;
  changefreq?: "hourly" | "daily" | "monthly";
  priority?: number;
};
/** Both XML vocabularies require at least one child entry in the published XSD. */
export function sitemapDocument(
  root: "sitemapindex" | "urlset",
  locations: (string | SitemapEntry)[],
) {
  if (!locations.length || locations.length > maxLocations)
    throw new Error("Invalid sitemap count");
  const child = root === "sitemapindex" ? "sitemap" : "url";
  const entries = locations.map((entry) => {
    const {
      loc: location,
      lastmod,
      changefreq,
      priority,
    } = typeof entry === "string" ? { loc: entry } : entry;
    const url = new URL(location);
    if (
      url.origin !== publicSiteOrigin() ||
      url.username ||
      url.password ||
      url.hash ||
      /%(?![a-f0-9]{2})/i.test(location) ||
      /[\x00-\x20\x7f]/.test(location)
    )
      throw new Error("Invalid sitemap URL");
    // Serialize a percent-encoded URI first; XML entity escaping is a separate step.
    const uri = url.href;
    if (uri.length < 12 || uri.length >= 2048)
      throw new Error("Sitemap URL exceeds protocol limit");
    if (
      lastmod &&
      (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(lastmod) ||
        !Number.isFinite(Date.parse(lastmod)) ||
        new Date(lastmod).toISOString() !== lastmod)
    )
      throw new Error("Invalid sitemap modification date");
    if (priority !== undefined && (!Number.isFinite(priority) || priority < 0 || priority > 1))
      throw new Error("Invalid sitemap priority");
    if (changefreq && !["hourly", "daily", "monthly"].includes(changefreq))
      throw new Error("Invalid sitemap change frequency");
    if (root === "sitemapindex" && (changefreq || priority !== undefined))
      throw new Error("Page hints are not valid on sitemap index entries");
    return `  <${child}>\n    <loc>${xmlEscape(uri)}</loc>${lastmod ? `\n    <lastmod>${lastmod}</lastmod>` : ""}${changefreq ? `\n    <changefreq>${changefreq}</changefreq>` : ""}${priority !== undefined ? `\n    <priority>${priority.toFixed(1)}</priority>` : ""}\n  </${child}>`;
  });
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<${root} xmlns="${namespace}">\n${entries.join("\n")}\n</${root}>\n`;
  if (Buffer.byteLength(body, "utf8") > sitemapMaxBytes)
    throw new Error("Sitemap exceeds protocol size limit");
  return body;
}
export function xmlEscape(value: string) {
  return value.replace(
    /[<>&"']/g,
    (character) =>
      ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[character]!,
  );
}
async function inventory(path: string) {
  try {
    return { data: await inventoryApi.read<Record<string, unknown>>(path) } as const;
  } catch (error) {
    return { error: error instanceof UserApiError && error.status === 404 ? 404 : 503 } as const;
  }
}

/** Enumerated route parameters produce their declared canonical pages without a URL list. */
function pageParameters(definition: ReaderDestinationDefinition) {
  return definition.segments.reduce<Record<string, string>[]>(
    (variants, segment) => {
      if (typeof segment === "string") return variants;
      const values = readerDestinationContract.parameters[segment.type]?.values;
      if (!values) throw new Error("A static sitemap page requires finite route parameters");
      return variants.flatMap((parameters) =>
        Object.keys(values).map((value) => ({ ...parameters, [segment.name]: value })),
      );
    },
    [{}],
  );
}

function pageEntries(latest: Record<string, string>): SitemapEntry[] {
  return readerDestinationContract.routes.flatMap((definition) => {
    const policy = definition.sitemap;
    if (policy?.kind !== "page") return [];
    return pageParameters(definition).map((parameters) => {
      const path = serializeReaderDestination({
        routeId: definition.id,
        parameters,
        query: {},
      });
      if (!path) throw new Error("Invalid sitemap page definition");
      const publication = policy.publication;
      let lastmod: string;
      if (publication.source === "page") {
        if (!definition.page?.updated) throw new Error("Missing page publication date");
        lastmod = publicationDate(`${definition.page.updated}T00:00:00.000Z`);
      } else {
        const key = publication.parameter
          ? `${publication.key}:${parameters[publication.parameter]}`
          : publication.key;
        lastmod = publicationDate(latest[key]);
      }
      return {
        loc: publicSiteOrigin() + path,
        lastmod,
        changefreq: policy.changefreq,
        priority: policy.priority,
      };
    });
  });
}
function xmlResponse(request: Request, body: string, seconds: number) {
  const etag = '"' + createHash("sha256").update(body).digest("hex") + '"';
  const headers = {
    "Content-Type": "application/xml; charset=utf-8",
    "Cache-Control": `public, max-age=${seconds}, s-maxage=${seconds}`,
    ETag: `W/${etag}`,
  };
  const matches = request.headers
    .get("if-none-match")
    ?.split(",")
    .some((value) => value.trim().replace(/^W\//, "") === etag || value.trim() === "*");
  return new Response(matches ? null : body, { status: matches ? 304 : 200, headers });
}
function unavailable(status = 503) {
  return new Response(status === 404 ? "Sitemap not found" : "Sitemap temporarily unavailable", {
    status,
    headers: { "Cache-Control": "no-store", ...(status === 503 ? { "Retry-After": "5" } : {}) },
  });
}
export async function sitemapIndex(request: Request) {
  try {
    const result = await inventory("/v1/sitemaps");
    if (result.error) return unavailable(result.error);
    const {
      generation,
      parts,
      latest_publication = {},
    } = result.data as {
      generation: string;
      latest_publication?: Record<string, string>;
      parts: { kind: string; page: number; modified_at?: number }[];
    };
    if (!/^[a-f0-9]{32}$/.test(generation) || !Array.isArray(parts) || parts.length >= maxLocations)
      return unavailable();
    const entries = parts.map(({ kind, page, modified_at }) => {
      if (
        !sitemapKinds.some((value) => value === kind) ||
        !Number.isSafeInteger(page) ||
        page < 1 ||
        page > 50000
      )
        throw new Error("Invalid sitemap part");
      if (modified_at !== undefined && (!Number.isFinite(modified_at) || modified_at <= 0))
        throw new Error("Invalid sitemap modification date");
      return {
        loc: `${publicSiteOrigin()}/sitemap-${kind}-${page}.xml`,
        ...(modified_at ? { lastmod: new Date(modified_at * 1000).toISOString() } : {}),
      };
    });
    if (new Set(entries.map((entry) => entry.loc)).size !== entries.length) return unavailable();
    return xmlResponse(
      request,
      sitemapDocument("sitemapindex", [
        {
          loc: `${publicSiteOrigin()}/sitemap-pages.xml`,
          lastmod: [
            pageBuildTime,
            ...pageEntries(latest_publication).map((entry) => entry.lastmod!),
          ]
            .sort()
            .at(-1)!,
        },
        ...entries,
      ]),
      60,
    );
  } catch {
    return unavailable();
  }
}
export async function sitemapPart(request: Request, kind: string, page: string) {
  const inventoryPage = inventoryPages.get(kind);
  if (!inventoryPage || !/^[1-9]\d{0,4}$/.test(page) || Number(page) > 50000)
    return unavailable(404);
  if (new URL(request.url).searchParams.has("v"))
    return new Response(null, {
      status: 308,
      headers: { Location: `${publicSiteOrigin()}/sitemap-${kind}-${page}.xml` },
    });
  try {
    const result = await inventory(`/v1/sitemaps/${kind}/${page}`);
    if (result.error) return unavailable(result.error);
    const { paths, entries: records } = result.data as {
      paths: string[];
      entries?: { path: string; lastmod?: string }[];
    };
    if (records !== undefined && (!Array.isArray(records) || records.length !== paths.length))
      return unavailable();
    if (!Array.isArray(paths) || paths.length > 1000) return unavailable();
    if (!paths.length) return unavailable(404);
    const entries = paths.map((path, index) => {
      const matched = typeof path === "string" ? matchReaderDestinationPath(path) : null;
      if (!matched || matched.definition.id !== inventoryPage.definition.id || path.endsWith("/"))
        throw new Error("Invalid sitemap path");
      const record = records?.[index];
      if (record && record.path !== path) throw new Error("Invalid sitemap metadata");
      return {
        loc: publicSiteOrigin() + path,
        ...(record?.lastmod ? { lastmod: publicationDate(record.lastmod) } : {}),
        changefreq: inventoryPage.policy.changefreq,
        priority: inventoryPage.policy.priority,
      };
    });
    return xmlResponse(request, sitemapDocument("urlset", entries), 300);
  } catch {
    return unavailable();
  }
}

export async function sitemapPages(request: Request) {
  try {
    const result = await inventory("/v1/sitemaps");
    if (result.error) return unavailable(result.error);
    const latest = (result.data.latest_publication ?? {}) as Record<string, string>;
    return xmlResponse(request, sitemapDocument("urlset", pageEntries(latest)), 300);
  } catch {
    return unavailable();
  }
}
