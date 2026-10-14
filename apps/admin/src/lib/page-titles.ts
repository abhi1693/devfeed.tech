import type { AdminRoute } from "./routes";
import { humanize, resourceGroups, resources } from "./resources";

export const adminSiteTitle = "DevFeed Admin";
export function pageTitle(title: string) {
  return `${title.replace(/\s+/g, " ").trim()} · ${adminSiteTitle}`;
}

/** The same titles serve server metadata and already-loaded record headings. */
export function adminRouteTitle(route: AdminRoute, recordName?: unknown): string {
  const name = typeof recordName === "string" ? recordName.trim() : "";
  switch (route.view) {
    case "group":
      return resourceGroups[route.group].label;
    case "source-import":
      return "Import sources";
    case "source-import-review":
      return "Review imported publisher";
    case "import":
      return "Import topics";
    case "proposal":
      return name ? `Review ${name} · Topic proposals` : "Review topic proposal";
    case "enrich":
      return name ? `Enrich ${name} · Topics` : "Enrich topic keywords";
    case "relationship-discover":
      return "Discover relationships";
    case "relationship-proposals":
      return "Relationship proposals";
    case "relationship-proposal":
      return name ? `Review ${name} · Relationship proposals` : "Review relationship";
  }
  const resource = "identity" in route ? route.identity.resource : route.resource;
  const spec = resources[resource];
  const label =
    resource === "analysis-jobs"
      ? route.view === "list"
        ? route.analysisType
          ? `${route.analysisType === "articles" ? "Article" : "Topic"} AI analysis`
          : spec.label
        : "identity" in route && route.identity.resource === "analysis-jobs"
          ? `${route.identity.kind === "topic-analysis" ? "Topic" : "Article"} AI analysis`
          : spec.label
      : spec.label;
  if (route.view === "list") return label;
  if (route.view === "new") return `Add ${spec.singular.toLowerCase()}`;
  const subject =
    spec.readonly && resource !== "users" && "id" in route.identity
      ? `Run ${route.identity.id.slice(0, 8)} · ${label}`
      : route.identity.resource === "topic-relations"
        ? `${humanize(route.identity.relation)} · ${spec.singular}`
        : name
          ? `${name} · ${spec.singular}`
          : spec.singular;
  if (route.view === "detail")
    return route.section === "details"
      ? subject
      : `${route.section === "related" ? "Related objects" : humanize(route.section)} · ${subject}`;
  return `${humanize(route.view === "workflow" ? route.action : route.view)} ${subject}`;
}
