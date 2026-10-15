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

/** The account section router owns the finite templates consumed by telemetry generation. */
export const partnerAccountSections = [
  { segments: [], resource: "account", action: "view" },
  { segments: ["details"], resource: "account", action: "view" },
  { segments: ["edit"], resource: "account", action: "edit" },
  { segments: ["related"], resource: "related", action: "list" },
  { segments: ["products"], resource: "product", action: "list" },
  { segments: ["campaigns"], resource: "campaign", action: "list" },
  { segments: ["campaigns", "new"], resource: "campaign", action: "create" },
  { segments: ["members", "new"], resource: "member", action: "create" },
  { segments: ["assets", "new"], resource: "asset", action: "create" },
  { segments: ["campaigns", ":identifier", "edit"], resource: "campaign", action: "edit" },
  { segments: ["assets", ":identifier", "edit"], resource: "asset", action: "edit" },
] as const;

/** Decode Next's path segments once, before passing a serializable route to the client. */
export function resolvePartnerAccountRoute(
  accountId: string,
  segments: readonly string[] = [],
): PartnerAccountRoute | undefined {
  if (!safeSegment(accountId) || segments.some((value) => !safeSegment(value))) return;
  const definition = partnerAccountSections.find(
    (candidate) =>
      candidate.segments.length === segments.length &&
      candidate.segments.every(
        (segment, index) => segment === ":identifier" || segment === segments[index],
      ),
  );
  if (!definition) return;
  const { resource, action } = definition;
  if ((resource === "asset" || resource === "campaign") && action === "edit")
    return { resource, action, accountId, identifier: segments[1] };
  return { resource, action, accountId } as PartnerAccountRoute;
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
