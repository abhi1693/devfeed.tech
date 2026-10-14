export const accountsPath = "/partnerships/accounts";
export const accountsTrail = [{ label: "Partnerships", href: "/partnerships" }];
export const accountHref = (id: string) => `${accountsPath}/${encodeURIComponent(id)}`;

type AccountScope = { accountId: string };
export type PartnerAccountRoute =
  | { resource: "account"; action: "create" }
  | (AccountScope & { resource: "account"; action: "view" | "edit" })
  | (AccountScope & { resource: "related" | "product"; action: "list" })
  | (AccountScope & { resource: "campaign"; action: "list" | "create" })
  | (AccountScope & { resource: "campaign"; action: "edit"; identifier: string })
  | (AccountScope & { resource: "member"; action: "create" })
  | (AccountScope & { resource: "asset"; action: "create" })
  | (AccountScope & { resource: "asset"; action: "edit"; identifier: string });

function safeSegment(value: string): boolean {
  return (
    value.length > 0 &&
    value.isWellFormed() &&
    value !== "." &&
    value !== ".." &&
    !/[/\\?#%\u0000-\u0020\u007f]/u.test(value)
  );
}

/** Decode Next's path segments once, before passing a serializable route to the client. */
export function resolvePartnerAccountRoute(
  accountId: string,
  segments: readonly string[] = [],
): PartnerAccountRoute | undefined {
  if (!safeSegment(accountId) || segments.some((value) => !safeSegment(value))) return;
  if (segments.length === 0) return { resource: "account", action: "view", accountId };
  if (segments.length === 1) {
    switch (segments[0]) {
      case "details":
        return { resource: "account", action: "view", accountId };
      case "edit":
        return { resource: "account", action: "edit", accountId };
      case "related":
        return { resource: "related", action: "list", accountId };
      case "products":
        return { resource: "product", action: "list", accountId };
      case "campaigns":
        return { resource: "campaign", action: "list", accountId };
    }
  }
  if (segments.length === 2 && segments[1] === "new") {
    switch (segments[0]) {
      case "campaigns":
        return { resource: "campaign", action: "create", accountId };
      case "members":
        return { resource: "member", action: "create", accountId };
      case "assets":
        return { resource: "asset", action: "create", accountId };
    }
  }
  if (segments.length === 3 && segments[2] === "edit") {
    switch (segments[0]) {
      case "campaigns":
        return { resource: "campaign", action: "edit", accountId, identifier: segments[1] };
      case "assets":
        return { resource: "asset", action: "edit", accountId, identifier: segments[1] };
    }
  }
}

/** Links and React remount keys use the decoded meaning, never an unchecked section string. */
export function partnerAccountHref(route: PartnerAccountRoute): string {
  if (route.resource === "account" && route.action === "create") return `${accountsPath}/new`;
  const base = accountHref(route.accountId);
  switch (route.resource) {
    case "account":
      return base + (route.action === "edit" ? "/edit" : "");
    case "related":
      return `${base}/related`;
    case "product":
      return `${base}/products`;
    case "member":
      return `${base}/members/new`;
    case "campaign":
      if (route.action === "edit")
        return `${base}/campaigns/${encodeURIComponent(route.identifier)}/edit`;
      return route.action === "create" ? `${base}/campaigns/new` : `${base}/campaigns`;
    case "asset":
      return route.action === "create"
        ? `${base}/assets/new`
        : `${base}/assets/${encodeURIComponent(route.identifier)}/edit`;
  }
}
