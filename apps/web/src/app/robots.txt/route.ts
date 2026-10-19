import { publicSiteOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET(): Response {
  // Use a text route because the metadata API cannot serialize Content-Signal.
  // HTML pages with noindex must remain crawlable for that directive to be seen.
  // Authentication protects account data; robots.txt is not an access control.
  return new Response(
    [
      "User-Agent: *",
      "Content-Signal: ai-train=no, search=yes, ai-input=no",
      "Allow: /",
      "Disallow: /api/",
      "",
      `Sitemap: ${publicSiteOrigin()}/sitemap.xml`,
      "",
    ].join("\n"),
    { headers: { "Content-Type": "text/plain; charset=utf-8" } },
  );
}
