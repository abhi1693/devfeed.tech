export type PortalSection = "overview" | "performance" | "assets";

export function portalPath(accountId: string, section: PortalSection = "overview") {
  const suffix = section === "overview" ? "" : `/${section}`;
  return accountId
    ? `/${encodeURIComponent(accountId)}${suffix}`
    : section === "overview"
      ? "/"
      : suffix;
}
