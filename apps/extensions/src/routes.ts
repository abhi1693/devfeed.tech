import { contentTypeFromRoute } from "../../web/src/lib/feed-query";

export type CatalogKind = "topics" | "sources";
export type SettingsPage =
  "profile" | "appearance" | "feed" | "notifications" | "topics" | "sources";

export type ExtensionRoute =
  | { type: "local"; page: "catalog"; catalog: CatalogKind }
  | { type: "local"; page: "settings"; settings: SettingsPage }
  | { type: "local"; page: "source-suggestion" }
  | { type: "article"; slug: string }
  | { type: "profile"; username: string }
  | {
      type: "reader";
      page: "personal" | "bookmarks" | "search" | "feed";
      detail?: { kind: CatalogKind; slug: string; contentType?: string };
      contentType?: string;
    };

const settingsRoutes: Record<string, SettingsPage> = {
  "/settings": "profile",
  "/settings/profile": "profile",
  "/settings/appearance": "appearance",
  "/settings/feed": "feed",
  "/settings/notifications": "notifications",
  "/settings/topics": "topics",
  "/settings/sources": "sources",
};

const catalogDetail = /^\/(topics|sources)\/([a-z0-9][a-z0-9-]{0,199})(?:\/([a-z-]+))?$/i;
const articleDetail = /^\/articles\/([a-z0-9][a-z0-9-]{0,199})$/i;
const publicProfile = /^\/users\/([a-z0-9][a-z0-9_-]{1,28}[a-z0-9])$/i;

/** Classifies every route rendered inside the new-tab reader. */
export function extensionRoute(pathname: string): ExtensionRoute | null {
  if (pathname === "/") return { type: "reader", page: "personal" };
  if (pathname === "/latest") return { type: "reader", page: "feed" };
  if (pathname === "/search") return { type: "reader", page: "search" };
  if (pathname === "/read-later") return { type: "reader", page: "bookmarks" };
  if (pathname === "/sources/suggest") return { type: "local", page: "source-suggestion" };

  const settings = settingsRoutes[pathname];
  if (settings) return { type: "local", page: "settings", settings };

  if (pathname === "/topics" || pathname === "/sources")
    return { type: "local", page: "catalog", catalog: pathname.slice(1) as CatalogKind };

  const article = articleDetail.exec(pathname);
  if (article) return { type: "article", slug: article[1] };

  const profile = publicProfile.exec(pathname);
  if (profile) return { type: "profile", username: profile[1] };

  const detail = catalogDetail.exec(pathname);
  if (detail)
    return {
      type: "reader",
      page: "feed",
      detail: {
        kind: detail[1] as CatalogKind,
        slug: detail[2],
        contentType: contentTypeFromRoute(detail[3] ?? ""),
      },
    };

  const contentType = contentTypeFromRoute(pathname.slice(1));
  return contentType ? { type: "reader", page: "feed", contentType } : null;
}
