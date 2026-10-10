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

export function partnerApiOrigin() {
  return requiredOrigin("DEVFEED_PARTNER_API_URL");
}
export function partnerWebOrigin() {
  return requiredOrigin("DEVFEED_PARTNER_BASE_URL");
}
