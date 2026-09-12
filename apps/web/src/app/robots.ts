import type { MetadataRoute } from "next";
import { publicSiteOrigin } from "@/lib/server/config";
export const dynamic = "force-dynamic";
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // HTML pages with noindex must remain crawlable for that directive to be seen.
      // Authentication protects account data; robots.txt is not an access control.
      disallow: ["/api/"],
    },
    sitemap: `${publicSiteOrigin()}/sitemap.xml`,
  };
}
