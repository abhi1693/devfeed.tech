// Keep standard campaign attribution across local redirects without forwarding
// arbitrary query parameters or imposing a platform-specific campaign taxonomy.
const parameters = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_id",
  "utm_term",
  "utm_content",
  "utm_source_platform",
  "utm_creative_format",
  "utm_marketing_tactic",
] as const;

export function attributionQuery(query: Record<string, string | string[] | undefined>) {
  const result = new URLSearchParams();
  for (const key of parameters) {
    const value = query[key];
    // Repeated parameters are ambiguous. Do not choose a different first/last value
    // from the analytics receiver, and never forward control characters.
    if (typeof value !== "string" || !value.trim() || value.length > 200) continue;
    if (/[\u0000-\u001f\u007f]/.test(value)) continue;
    result.set(key, value);
  }
  return result.toString();
}

export function anonymousFeedDestination(query: Record<string, string | string[] | undefined>) {
  const attribution = attributionQuery(query);
  return `/latest${attribution ? `?${attribution}` : ""}`;
}
