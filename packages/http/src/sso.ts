/** Accept only an absolute provider URL returned by the authenticated logout operation. */
export function ssoLogoutUrl(value: unknown): string {
  if (!value || typeof value !== "object" || !("url" in value) || typeof value.url !== "string")
    throw new Error("Invalid sign-out response");
  const url = new URL(value.url);
  if (
    url.username ||
    url.password ||
    (url.protocol !== "https:" &&
      !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))
  )
    throw new Error("Invalid provider sign-out URL");
  return url.href;
}

/** Keep provider URL validation in front of the browser's navigation side effect. */
export function navigateToSSOLogout(
  value: unknown,
  navigate: (url: string) => void = (url) => window.location.assign(url),
): void {
  navigate(ssoLogoutUrl(value));
}
