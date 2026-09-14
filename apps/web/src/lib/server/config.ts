import "server-only";

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

export function userRequestOriginAllowed(origin: string | null) {
  const extension = extensionOriginAllowed(origin);
  return origin === userWebOrigin() || extension;
}

export function publicSiteOrigin() {
  return process.env.DEVFEED_USER_BASE_URL ? userWebOrigin() : "https://devfeed.tech";
}

export function analyticsMeasurementId() {
  if (process.env.NODE_ENV !== "production") return "";
  // Preserve deployed production defaults; Compose explicitly opts out at runtime.
  const enabled = process.env.DEVFEED_ANALYTICS_ENABLED;
  if (enabled !== undefined && enabled.trim().toLowerCase() !== "true") return "";
  return process.env.GOOGLE_ANALYTICS_ID?.trim() || "G-N4V5CW5C0M";
}
