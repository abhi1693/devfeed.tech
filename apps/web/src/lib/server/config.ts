import "server-only";
import { mcpEndpoint } from "../mcp";

function requiredOrigin(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  const url = new URL(value);
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new Error(`${name} must be an origin`);
  return url.origin;
}

export function userApiOrigin() {
  return requiredOrigin("DEVFEED_USER_API_URL");
}
export function userWebOrigin() {
  return requiredOrigin("DEVFEED_USER_BASE_URL");
}

export function extensionOriginAllowed(origin: string | null) {
  const ids: unknown = JSON.parse(process.env.DEVFEED_USER_EXTENSION_IDS || "[]");
  if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string" || !/^[a-p]{32}$/.test(id)))
    throw new Error("DEVFEED_USER_EXTENSION_IDS must contain exact Chrome extension IDs");
  return ids.some((id) => origin === `chrome-extension://${id}`);
}

function additionalUserOrigins(): string[] {
  const values: unknown = JSON.parse(process.env.DEVFEED_USER_ALLOWED_ORIGINS || "[]");
  if (!Array.isArray(values))
    throw new Error("DEVFEED_USER_ALLOWED_ORIGINS must be an origin list");
  return values.map((value: unknown) => {
    if (
      typeof value !== "string" ||
      /[\s\\]/u.test(value) ||
      Array.from(value).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) ||
      !/^https?:\/\/[^/?#]+\/?$/i.test(value)
    )
      throw new Error("DEVFEED_USER_ALLOWED_ORIGINS must contain exact HTTP(S) origins");
    const url = new URL(value);
    if (
      value.includes("@") ||
      url.username ||
      url.password ||
      !url.hostname ||
      url.hostname.includes("*") ||
      /:\/?$/.test(value)
    )
      throw new Error("DEVFEED_USER_ALLOWED_ORIGINS must contain exact HTTP(S) origins");
    return url.origin;
  });
}

export function userRequestOriginAllowed(origin: string | null) {
  const extension = extensionOriginAllowed(origin);
  const additional = additionalUserOrigins();
  return (
    origin === userWebOrigin() || (origin !== null && additional.includes(origin)) || extension
  );
}

export function publicSiteOrigin() {
  return process.env.DEVFEED_USER_BASE_URL ? userWebOrigin() : "https://devfeed.tech";
}

export function publicApiOrigin() {
  return process.env.DEVFEED_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
}

export function mcpPublicUrl() {
  const value = process.env.DEVFEED_MCP_PUBLIC_URL?.trim();
  if (!value) return null;
  const url = mcpEndpoint(value);
  if (!url) throw new Error("DEVFEED_MCP_PUBLIC_URL must be a public HTTP or HTTPS URL");
  return url;
}

export function analyticsMeasurementId() {
  if (process.env.NODE_ENV !== "production") return "";
  // Preserve deployed production defaults; Compose explicitly opts out at runtime.
  const enabled = process.env.DEVFEED_ANALYTICS_ENABLED;
  if (enabled !== undefined && enabled.trim().toLowerCase() !== "true") return "";
  return process.env.GOOGLE_ANALYTICS_ID?.trim() || "G-N4V5CW5C0M";
}

export function xPixelEnabled() {
  return process.env.DEVFEED_X_PIXEL_ENABLED?.trim().toLowerCase() === "true";
}

export function clarityProjectId() {
  if (process.env.NODE_ENV !== "production") return "";
  const enabled = process.env.DEVFEED_ANALYTICS_ENABLED;
  if (enabled !== undefined && enabled.trim().toLowerCase() !== "true") return "";
  return process.env.MICROSOFT_CLARITY_PROJECT_ID?.trim() || "";
}
