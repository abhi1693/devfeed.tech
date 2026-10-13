export type PortalSection = "overview" | "performance" | "products" | "connections" | "settings";
export type ProductRoute = {
  view: "list" | "create" | "detail" | "placements" | "edit" | "archive";
  productId?: string;
};

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
    action,
    accountId,
    query = "",
  }: { edit?: boolean; action?: "edit" | "archive"; accountId?: string; query?: string } = {},
) {
  const params = new URLSearchParams(query);
  if (accountId) params.set("account_id", accountId);
  else params.delete("account_id");
  return `/products/${encodeURIComponent(productId)}${action ? `/${action}` : edit ? "/placements" : ""}${params.size ? `?${params}` : ""}`;
}
