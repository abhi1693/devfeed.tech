import { resolvePageRoute } from "./page-routes";
import { pageRouteCatalogs } from "./page-routes.generated";
export { redactAuthText } from "./auth-privacy";
const text = (value: unknown): string => (typeof value === "string" ? value : "");
export type TelemetryApp = keyof typeof pageRouteCatalogs;
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
export function methodName(value: unknown): string {
  const method = text(value).toUpperCase();
  return ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].includes(method)
    ? method
    : "OTHER";
}
