// Shared by the browser and the receiver. Never trust browser-supplied telemetry.
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as ObjectValue)
    : {};
const array = (value: unknown): unknown[] => (Array.isArray(value) ? value.slice(0, 100) : []);
const text = (value: unknown): string => (typeof value === "string" ? value : "");
const number = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;
const hex = (value: unknown, length: number) =>
  new RegExp(`^[a-f0-9]{${length}}$`, "i").test(text(value)) ? text(value) : undefined;
const errorTypes = new Set([
  "Error",
  "TypeError",
  "RangeError",
  "ReferenceError",
  "SyntaxError",
  "URIError",
  "EvalError",
  "AggregateError",
  "UnhandledRejection",
  "ChunkLoadError",
]);
const pages = new Set([
  "/",
  "/login",
  "/register",
  "/search",
  "/articles",
  "/trending",
  "/my-feed",
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
  "/knowledge/graph",
  "/news",
  "/tutorials",
  "/research",
  "/videos",
  "/podcasts",
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
    path = new URL(text(value), "https://redacted.invalid").pathname.replace(/\/$/, "") || "/";
  } catch {
    return "unmatched";
  }
  if (pages.has(path)) return path;
  if (/^\/(articles|sources|topics|tags|workers)\/[^/]+(?:\/[^/]+)?$/.test(path)) {
    const [, root, , kind] = path.split("/");
    return `/${root}/:id${kind ? "/:type" : ""}`;
  }
  const api = path.match(/^(?:\/api)?\/v1\/(?:(admin|user)\/)?([^/]+)(\/.*)?$/);
  if (api && resources.has(api[2]))
    return `/v1/${api[1] ? `${api[1]}/` : ""}${api[2]}${api[3] ? "/:path" : ""}`;
  if (path.startsWith("/_next/static/")) return "/_next/static/:asset";
  if (path === "/_next/image") return path;
  if (/^\/settings\/[^/]+$/.test(path)) return "/settings/:section";
  return "unmatched";
}
function trace(value: unknown) {
  const input = object(value);
  const trace_id = hex(input.trace_id, 32);
  const span_id = hex(input.span_id, 16);
  return trace_id && span_id ? { trace_id, span_id } : undefined;
}
function timestamp(value: unknown) {
  const parsed = Date.parse(text(value));
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : new Date().toISOString();
}
function frame(value: unknown) {
  const input = object(value);
  // Only immutable Next build assets; no arbitrary host, query, fragment or user source.
  const match = text(input.filename).match(
    /(?:https?:\/\/[^/]+)?(\/_next\/static\/[a-zA-Z0-9_./-]+\.js)(?:[?#].*)?$/,
  );
  if (!match || match[1].includes("..")) return null;
  return {
    filename: match[1],
    function: "<redacted>",
    lineno: number(input.lineno),
    colno: number(input.colno),
  };
}
export function sanitizePayload(type: string, value: unknown): ObjectValue | null {
  const input = object(value);
  const common = { timestamp: timestamp(input.timestamp), trace: trace(input.trace) };
  if (type === "exception")
    return {
      ...common,
      type: errorTypes.has(text(input.type)) ? text(input.type) : "Error",
      value: "Browser error (message redacted)",
      fatal: input.fatal === true,
      stacktrace: { frames: array(object(input.stacktrace).frames).map(frame).filter(Boolean) },
    };
  if (type === "measurement") {
    const allowed = new Set(["web-vitals", "navigation", "performance", "resource"]);
    if (!allowed.has(text(input.type))) return null;
    const values = Object.fromEntries(
      Object.entries(object(input.values)).filter(
        ([key, val]) =>
          /^(?:lcp|fcp|cls|inp|ttfb|fid|ttfi|tti|tbt|domComplete|domInteractive|loadEventEnd|duration)$/i.test(
            key,
          ) && number(val) !== undefined,
      ),
    );
    return Object.keys(values).length ? { ...common, type: input.type, values } : null;
  }
  if (type === "event") {
    const names = new Set([
      "session_start",
      "view_changed",
      "page_view",
      "route_change",
      "telemetry_ready",
    ]);
    if (!names.has(text(input.name))) return null;
    return {
      ...common,
      name: input.name,
      attributes: { route: routeName(object(input.attributes).url) },
    };
  }
  if (type === "trace") return sanitizeTraces(input);
  // Console messages, arbitrary actions, DOM text and custom contexts are deliberately excluded.
  return null;
}
function sanitizeTraces(input: ObjectValue): ObjectValue {
  return {
    resourceSpans: array(input.resourceSpans).map((resource) => ({
      resource: { attributes: [] },
      scopeSpans: array(object(resource).scopeSpans).map((scope) => ({
        scope: { name: "devfeed-browser" },
        spans: array(object(scope).spans)
          .map((value) => {
            const span = object(value);
            const attributes = array(span.attributes)
              .map((value) => {
                const attr = object(value);
                const val = object(attr.value);
                if (["http.method", "http.request.method"].includes(text(attr.key))) {
                  return {
                    key: "http.request.method",
                    value: { stringValue: methodName(val.stringValue) },
                  };
                }
                if (["http.status_code", "http.response.status_code"].includes(text(attr.key))) {
                  const code = Number(val.intValue);
                  return code >= 100 && code <= 599
                    ? { key: "http.response.status_code", value: { intValue: code } }
                    : null;
                }
                if (["http.url", "url.full", "http.target"].includes(text(attr.key))) {
                  return { key: "http.route", value: { stringValue: routeName(val.stringValue) } };
                }
                return null;
              })
              .filter(Boolean);
            const nanos = (v: unknown) => (/^\d{1,20}$/.test(text(v)) ? text(v) : undefined);
            return {
              traceId: hex(span.traceId, 32),
              spanId: hex(span.spanId, 16),
              parentSpanId: hex(span.parentSpanId, 16),
              name: "browser.request",
              kind: 3,
              startTimeUnixNano: nanos(span.startTimeUnixNano),
              endTimeUnixNano: nanos(span.endTimeUnixNano),
              attributes,
              status: {
                code: [0, 1, 2].includes(Number(object(span.status).code))
                  ? Number(object(span.status).code)
                  : 0,
              },
            };
          })
          .filter(
            (span) => span.traceId && span.spanId && span.startTimeUnixNano && span.endTimeUnixNano,
          ),
      })),
    })),
  };
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
export function sanitizeMeta(value: unknown, settings: BrowserSettings, preserveSampling = false) {
  const meta = object(value);
  const id = text(object(meta.session).id);
  return {
    app: {
      name: `devfeed-${settings.app}`,
      version: settings.version,
      environment: settings.environment,
    },
    session: /^[a-f0-9-]{16,64}$/i.test(id)
      ? {
          id,
          // Faro's later SessionInstrumentation hook requires this internal boolean
          // and removes it before transport. The server never preserves it.
          ...(preserveSampling
            ? {
                attributes: {
                  isSampled:
                    object(object(meta.session).attributes).isSampled === "true" ? "true" : "false",
                },
              }
            : {}),
        }
      : undefined,
    page: { url: routeName(object(meta.page).url) },
    view: { name: routeName(object(meta.page).url) },
  };
}
export function sanitizeBody(value: unknown, settings: BrowserSettings) {
  const body = object(value);
  return {
    meta: sanitizeMeta(body.meta, settings),
    exceptions: array(body.exceptions)
      .map((v) => sanitizePayload("exception", v))
      .filter(Boolean),
    measurements: array(body.measurements)
      .map((v) => sanitizePayload("measurement", v))
      .filter(Boolean),
    events: array(body.events)
      .map((v) => sanitizePayload("event", v))
      .filter(Boolean),
    ...(body.traces ? { traces: sanitizePayload("trace", body.traces) } : {}),
  };
}
