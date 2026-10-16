// Shared by the browser and the receiver. Never trust browser-supplied telemetry.
import { redactAuthentication } from "./auth-privacy";
import { resolvePageRoute } from "./page-routes";
import { pageRouteCatalogs } from "./page-routes.generated";
export { redactAuthText } from "./auth-privacy";
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as ObjectValue)
    : {};
const array = (value: unknown): unknown[] => (Array.isArray(value) ? value.slice(0, 100) : []);
const text = (value: unknown): string => (typeof value === "string" ? value : "");
export type TelemetryApp = keyof typeof pageRouteCatalogs;
const approvedRouteNames = Object.fromEntries(
  Object.entries(pageRouteCatalogs).map(([app, routes]) => [
    app,
    new Set(["unmatched", ...routes.map(({ label }) => label)]),
  ]),
) as Record<TelemetryApp, Set<string>>;
const approvedName = (value: unknown, app: TelemetryApp): string =>
  approvedRouteNames[app].has(text(value)) ? text(value) : "unmatched";

export function routeName(value: unknown, app: TelemetryApp = "web"): string {
  if (!text(value)) return "unmatched";
  let path: string;
  try {
    path = new URL(text(value), "https://routes.invalid").pathname.replace(/\/$/, "") || "/";
  } catch {
    return "unmatched";
  }
  return resolvePageRoute(path, pageRouteCatalogs[app]) ?? "unmatched";
}
export function normalizePayload(
  type: string,
  value: unknown,
  settings?: BrowserSettings,
): ObjectValue | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const input = object(redactAuthentication(value));
  if (type === "event" && input.name === "route_change" && settings)
    return {
      ...input,
      attributes: { route: approvedName(object(input.attributes).route, settings.app) },
    };
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
    ...(meta.view
      ? {
          view: {
            ...object(meta.view),
            name: approvedName(object(meta.view).name, settings.app),
          },
        }
      : {}),
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
      .map((v) => normalizePayload("event", v, settings))
      .filter(Boolean),
    logs: array(body.logs)
      .map((v) => normalizePayload("log", v))
      .filter(Boolean),
    ...(body.traces ? { traces: normalizePayload("trace", body.traces, settings) } : {}),
  };
}
