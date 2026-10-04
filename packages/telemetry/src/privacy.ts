// Shared by the browser and the receiver. Never trust browser-supplied telemetry.
import { redactAuthentication } from "./auth-privacy";
export { redactAuthText } from "./auth-privacy";
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as ObjectValue)
    : {};
const array = (value: unknown): unknown[] => (Array.isArray(value) ? value.slice(0, 100) : []);
const text = (value: unknown): string => (typeof value === "string" ? value : "");
const pages = new Set([
  "/",
  "/login",
  "/register",
  "/search",
  "/articles",
  "/trending",
  "/my-feed",
  "/latest",
  "/sources",
  "/sources/suggest",
  "/topics",
  "/legal",
  "/legal/privacy",
  "/legal/terms",
  "/settings",
  "/settings/profile",
  "/settings/sources",
  "/settings/topics",
  "/settings/feed",
  "/settings/notifications",
  "/settings/appearance",
  "/queues",
  "/workers",
  "/start",
  "/news",
  "/tutorials",
  "/research",
  "/videos",
  "/podcasts",
  "/content",
  "/taxonomy",
  "/jobs",
  "/jobs/enrichment",
  "/users",
]);
const resources = new Set([
  "feed",
  "search",
  "articles",
  "sources",
  "topics",
  "tags",
  "workers",
  "jobs",
  "queues",
  "overview",
  "settings",
  "auth",
  "users",
  "notifications",
  "relationships",
  "topic-discovery",
  "research",
  "categories",
  "bookmarks",
  "preferences",
  "subscriptions",
  "account",
  "profile",
]);
export function routeName(value: unknown): string {
  if (!text(value)) return "unmatched";
  let path: string;
  try {
    path = new URL(text(value), "https://routes.invalid").pathname.replace(/\/$/, "") || "/";
  } catch {
    return "unmatched";
  }
  if (pages.has(path)) return path;
  if (/^\/(articles|sources|topics|tags|workers)\/[^/]+(?:\/[^/]+)?$/.test(path)) {
    const [, root, , kind] = path.split("/");
    return `/${root}/:id${kind ? "/:type" : ""}`;
  }
  const admin = path.match(
    /^\/(content\/(?:articles|sources)|taxonomy\/(?:topics|tags|relationships)|users|jobs\/(?:ingestion|enrichment\/(?:articles|images|sources)|analysis(?:\/(?:articles|topics))?|notifications))(?:\/(.*))?$/,
  );
  if (admin) {
    const suffix = admin[2]?.split("/");
    if (!suffix) return `/${admin[1]}`;
    if (suffix.length === 1 && ["new", "import", "discover", "proposals"].includes(suffix[0]))
      return `/${admin[1]}/${suffix[0]}`;
    if (suffix.length === 2 && suffix[0] === "proposals") return `/${admin[1]}/proposals/:id`;
    if (suffix.length === 1) return `/${admin[1]}/:id`;
    if (
      suffix.length === 2 &&
      [
        "edit",
        "delete",
        "enrich",
        "review",
        "classify",
        "fetch",
        "analysis",
        "related",
        "history",
        "evidence",
        "logs",
        "relevance",
        "topics",
        "sources",
        "likes",
        "interests",
        "recommendations",
      ].includes(suffix[1])
    )
      return `/${admin[1]}/:id/${suffix[1]}`;
    return "unmatched";
  }
  const api = path.match(/^(?:\/api)?\/v1\/(?:(admin|user)\/)?([^/]+)(\/.*)?$/);
  if (api && resources.has(api[2]))
    return `/v1/${api[1] ? `${api[1]}/` : ""}${api[2]}${api[3] ? "/:path" : ""}`;
  if (path.startsWith("/_next/static/")) return "/_next/static/:asset";
  if (path === "/_next/image") return path;
  if (/^\/settings\/[^/]+$/.test(path)) return "/settings/:section";
  return "unmatched";
}
export function normalizePayload(
  type: string,
  value: unknown,
  settings?: BrowserSettings,
): ObjectValue | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const input = object(redactAuthentication(value));
  if (type === "trace" && settings) {
    return {
      ...input,
      resourceSpans: array(input.resourceSpans).map((resource) => {
        const span = object(resource);
        const metadata = object(span.resource);
        const names = new Set(["service.name", "service.version", "deployment.environment.name"]);
        return {
          ...span,
          resource: {
            ...metadata,
            attributes: [
              ...array(metadata.attributes).filter((attr) => !names.has(text(object(attr).key))),
              { key: "service.name", value: { stringValue: `devfeed-${settings.app}-browser` } },
              { key: "service.version", value: { stringValue: settings.version } },
              { key: "deployment.environment.name", value: { stringValue: settings.environment } },
            ],
          },
        };
      }),
    };
  }
  return input;
}
export function methodName(value: unknown): string {
  const method = text(value).toUpperCase();
  return ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].includes(method)
    ? method
    : "OTHER";
}
export interface BrowserSettings {
  enabled: boolean;
  app: "web" | "admin";
  version: string;
  environment: string;
}
export function normalizeMeta(value: unknown, settings: BrowserSettings, preserveSampling = false) {
  const meta = object(redactAuthentication(value));
  const session = object(meta.session);
  const attributes = { ...object(session.attributes) };
  if (!preserveSampling) delete attributes.isSampled;
  return {
    ...meta,
    app: {
      ...object(meta.app),
      name: `devfeed-${settings.app}`,
      version: settings.version,
      environment: settings.environment,
    },
    ...(meta.session ? { session: { ...session, attributes } } : {}),
  };
}
export function normalizeBody(value: unknown, settings: BrowserSettings) {
  const body = object(redactAuthentication(value));
  return {
    ...body,
    meta: normalizeMeta(body.meta, settings),
    exceptions: array(body.exceptions)
      .map((v) => normalizePayload("exception", v))
      .filter(Boolean),
    measurements: array(body.measurements)
      .map((v) => normalizePayload("measurement", v))
      .filter(Boolean),
    events: array(body.events)
      .map((v) => normalizePayload("event", v))
      .filter(Boolean),
    logs: array(body.logs)
      .map((v) => normalizePayload("log", v))
      .filter(Boolean),
    ...(body.traces ? { traces: normalizePayload("trace", body.traces, settings) } : {}),
  };
}
