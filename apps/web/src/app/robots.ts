import type { MetadataRoute } from "next";
import { publicSiteOrigin } from "@/lib/server/config";
export const dynamic = "force-dynamic";
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/api/",
        "/settings",
        "/my-feed",
        "/search",
        "/login",
        "/register",
        "/sources/suggest",
      ],
    },
    sitemap: `${publicSiteOrigin()}/sitemap.xml`,
  };
}
