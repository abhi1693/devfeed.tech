export type PortalSection = "overview" | "performance" | "products" | "connections" | "settings";
export type ProductRoute = { view: "list" | "detail" | "placements"; productId?: string };

export function portalPath(section: PortalSection = "overview") {
  return section === "overview" ? "/" : `/${section}`;
}

export function accountSettingsPath(accountId: string) {
  return `/settings/${encodeURIComponent(accountId)}`;
}

export function productPath(
  productId: string,
  {
    edit = false,
    accountId,
    query = "",
  }: { edit?: boolean; accountId?: string; query?: string } = {},
) {
  const params = new URLSearchParams(query);
  if (accountId) params.set("account_id", accountId);
  else params.delete("account_id");
  return `/products/${encodeURIComponent(productId)}${edit ? "/placements" : ""}${params.size ? `?${params}` : ""}`;
}
