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
