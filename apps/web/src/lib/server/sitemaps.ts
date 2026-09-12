import "server-only";
import { createHash } from "node:crypto";
import { publicSiteOrigin } from "./config";

export const sitemapKinds = ["articles", "topics", "tags", "sources"] as const;
const namespace = "http://www.sitemaps.org/schemas/sitemap/0.9";
export const sitemapMaxBytes = 52_428_800;
const maxLocations = 50_000;
/** Both XML vocabularies require at least one child entry in the published XSD. */
export function sitemapDocument(root: "sitemapindex" | "urlset", locations: string[]) {
  if (!locations.length || locations.length > maxLocations)
    throw new Error("Invalid sitemap count");
  const child = root === "sitemapindex" ? "sitemap" : "url";
  const entries = locations.map((location) => {
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
    return `  <${child}><loc>${xmlEscape(uri)}</loc></${child}>`;
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
  const response = await fetch(
    new URL(path, process.env.DEVFEED_PUBLIC_API_URL ?? "http://127.0.0.1:8000"),
    {
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(55000),
      headers: { Accept: "application/json" },
    },
  );
  if (!response.ok) return { error: response.status === 404 ? 404 : 503 } as const;
  return { data: await response.json() } as const;
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
    const { generation, parts } = result.data as {
      generation: string;
      parts: { kind: string; page: number }[];
    };
    if (!/^[a-f0-9]{32}$/.test(generation) || !Array.isArray(parts) || parts.length >= maxLocations)
      return unavailable();
    const entries = parts.map(({ kind, page }) => {
      if (
        !sitemapKinds.some((value) => value === kind) ||
        !Number.isSafeInteger(page) ||
        page < 1 ||
        page > 50000
      )
        throw new Error("Invalid sitemap part");
      return `${publicSiteOrigin()}/sitemap-${kind}-${page}.xml?v=${generation}`;
    });
    if (new Set(entries).size !== entries.length) return unavailable();
    return xmlResponse(
      request,
      sitemapDocument("sitemapindex", [`${publicSiteOrigin()}/sitemap-pages.xml`, ...entries]),
      60,
    );
  } catch {
    return unavailable();
  }
}
export async function sitemapPart(request: Request, kind: string, page: string) {
  if (
    !sitemapKinds.some((value) => value === kind) ||
    !/^[1-9]\d{0,4}$/.test(page) ||
    Number(page) > 50000
  )
    return unavailable(404);
  const version = new URL(request.url).searchParams.get("v");
  if (version !== null && !/^[a-f0-9]{32}$/.test(version)) return unavailable(404);
  try {
    const result = await inventory(`/v1/sitemaps/${kind}/${page}${version ? `?v=${version}` : ""}`);
    if (result.error) return unavailable(result.error);
    const { paths } = result.data as { paths: string[] };
    if (!Array.isArray(paths) || paths.length > 1000) return unavailable();
    if (!paths.length) return unavailable(404);
    const entries = paths.map((path) => {
      if (
        typeof path !== "string" ||
        !path.startsWith(`/${kind}/`) ||
        !path.slice(kind.length + 2) ||
        path.slice(kind.length + 2).includes("/") ||
        [".", ".."].includes(decodeURIComponent(path.slice(kind.length + 2))) ||
        /[\x00-\x20\\?#]/.test(path)
      )
        throw new Error("Invalid sitemap path");
      return publicSiteOrigin() + path;
    });
    return xmlResponse(request, sitemapDocument("urlset", entries), 300);
  } catch {
    return unavailable();
  }
}

export function sitemapPages(request: Request) {
  return xmlResponse(request, sitemapDocument("urlset", [`${publicSiteOrigin()}/`]), 3600);
}
