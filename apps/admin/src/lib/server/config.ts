import "server-only";

function requiredOrigin(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password ||
      url.search || url.hash || url.pathname !== '/') throw new Error(`${name} must be an origin`);
  return url.origin;
}

export function adminApiOrigin() { return requiredOrigin("DEVFEED_ADMIN_API_URL"); }
export function adminWebOrigin() { return requiredOrigin("DEVFEED_ADMIN_BASE_URL"); }
