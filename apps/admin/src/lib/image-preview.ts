/** Browser-only previews; saving still uses the API's public-URL validation. */
export function imagePreviewUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.length > 2048 || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}
