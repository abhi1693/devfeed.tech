import "server-only";
import { createHash } from "node:crypto";
import { publicSiteOrigin } from "./config";

export const sitemapKinds = ["articles", "topics", "tags", "sources"] as const;
const namespace = "http://www.sitemaps.org/schemas/sitemap/0.9";
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
    ETag: etag,
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
    if (!/^[a-f0-9]{32}$/.test(generation) || !Array.isArray(parts) || parts.length > 50000)
      return unavailable();
    const entries = parts
      .map(({ kind, page }) => {
        if (
          !sitemapKinds.some((value) => value === kind) ||
          !Number.isSafeInteger(page) ||
          page < 1 ||
          page > 50000
        )
          throw new Error("Invalid sitemap part");
        return `<sitemap><loc>${xmlEscape(`${publicSiteOrigin()}/sitemap-${kind}-${page}.xml?v=${generation}`)}</loc></sitemap>`;
      })
      .join("");
    return xmlResponse(
      request,
      `<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="${namespace}">${entries}</sitemapindex>`,
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
    const entries = paths
      .map((path) => {
        if (
          typeof path !== "string" ||
          !path.startsWith(`/${kind}/`) ||
          path.length > 1800 ||
          /[\x00-\x20\\?#]/.test(path)
        )
          throw new Error("Invalid sitemap path");
        return `<url><loc>${xmlEscape(publicSiteOrigin() + path)}</loc></url>`;
      })
      .join("");
    return xmlResponse(
      request,
      `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="${namespace}">${entries}</urlset>`,
      300,
    );
  } catch {
    return unavailable();
  }
}
