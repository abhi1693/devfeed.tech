import type { Resource } from "./resources";

export const resourcePaths: Record<Resource, string> = {
  articles: "/content/articles",
  sources: "/content/sources",
  topics: "/taxonomy/topics",
  tags: "/taxonomy/tags",
  "topic-relations": "/taxonomy/relationships",
  "ingestion-jobs": "/jobs/ingestion",
  "article-jobs": "/jobs/enrichment/articles",
  "image-jobs": "/jobs/enrichment/images",
  "source-jobs": "/jobs/enrichment/sources",
  "analysis-jobs": "/jobs/analysis",
  "notification-jobs": "/jobs/notifications",
};
export type AnalysisType = "articles" | "topics";
export type DetailSection = "details" | "related" | "history" | "evidence" | "logs";
export type WorkflowAction = "review" | "classify" | "fetch";
export type RouteGroup = "content" | "taxonomy" | "jobs" | "enrichment";
export const groupPaths = { content: "/content", taxonomy: "/taxonomy", jobs: "/jobs", enrichment: "/jobs/enrichment" };
export const groupLabels = { content: "Content", taxonomy: "Taxonomy", jobs: "Jobs", enrichment: "Enrichment jobs" };
const content = new Set<Resource>(["articles", "sources", "topics", "tags", "topic-relations"]);
const segment = /^[a-zA-Z0-9_-]+$/;

export function resourceHref(resource: Resource, analysisType?: AnalysisType): string {
  return resourcePaths[resource] + (resource === "analysis-jobs" && analysisType ? `/${analysisType}` : "");
}

export function recordHref(resource: Resource, record: { id: string; kind?: unknown }, section: DetailSection = "details"): string {
  let path = resourceHref(resource);
  if (resource === "analysis-jobs") {
    const topic = record.kind === "topic-analysis" || record.id.startsWith("topic-analysis~");
    const id = record.id.replace(/^topic-analysis~/, "");
    path += `/${topic ? "topics" : "articles"}/${encodeURIComponent(id)}`;
  } else if (resource === "topic-relations") {
    const [from, to, relation] = record.id.split("~");
    path += `/${encodeURIComponent(from)}/${encodeURIComponent(relation)}/${encodeURIComponent(to)}`;
  } else path += `/${encodeURIComponent(record.id)}`;
  return path + (section === "details" ? "" : `/${section}`);
}

export function resourceTrail(resource: Resource) {
  const root = resourcePaths[resource].split("/")[1] as "content" | "taxonomy" | "jobs";
  return [
    { label: groupLabels[root], href: groupPaths[root] },
    ...(resourcePaths[resource].startsWith("/jobs/enrichment/") ? [{ label: "Enrichment jobs", href: groupPaths.enrichment }] : []),
  ];
}

export function detailSections(resource: Resource): DetailSection[] {
  return ["details", ...(content.has(resource) ? ["related" as const] : ["logs" as const]),
    ...(["articles", "sources"].includes(resource) ? ["history" as const] : []),
    ...(resource === "articles" ? ["evidence" as const] : [])];
}

export type AdminRoute =
  | { view: "group"; group: RouteGroup }
  | { view: "list"; resource: Resource; analysisType?: AnalysisType }
  | { view: "new"; resource: Resource }
  | { view: "detail"; resource: Resource; id: string; section: DetailSection }
  | { view: "edit" | "delete"; resource: Resource; id: string }
  | { view: "workflow"; resource: "articles" | "sources"; id: string; action: WorkflowAction }
  | { view: "relationship-discover" | "relationship-proposals" }
  | { view: "relationship-proposal"; id: string }
  | { view: "import" | "proposals" }
  | { view: "proposal" | "enrich"; id: string };

/** Decode browser paths once; resource keys and composite IDs remain API adapter details. */
export function resolveAdminRoute(parts: string[]): AdminRoute | undefined {
  if (parts.some(part => !segment.test(part))) return;
  const path = `/${parts.join("/")}`;
  for (const [group, prefix] of Object.entries(groupPaths)) if (path === prefix) return { view: "group", group: group as RouteGroup };
  for (const [key, prefix] of Object.entries(resourcePaths)) {
    const resource = key as Resource;
    if (path !== prefix && !path.startsWith(`${prefix}/`)) continue;
    const rest = parts.slice(prefix.split("/").length - 1);
    if (!rest.length) return { view: "list", resource };
    if (resource === "topics") {
      if (rest.length === 1 && rest[0] === "import") return { view: "import" };
      if (rest[0] === "proposals") return rest.length === 1 ? { view: "proposals" } : rest.length === 2 ? { view: "proposal", id: rest[1] } : undefined;
      if (rest.length === 2 && rest[1] === "enrich") return { view: "enrich", id: rest[0] };
    }
    if (resource === "topic-relations") {
      if (rest.length === 1 && rest[0] === "discover") return { view: "relationship-discover" };
      if (rest[0] === "proposals") return rest.length === 1 ? { view: "relationship-proposals" } : rest.length === 2 ? { view: "relationship-proposal", id: rest[1] } : undefined;
    }
    if (rest.length === 1 && rest[0] === "new") return content.has(resource) ? { view: "new", resource } : undefined;
    let id: string;
    let tail: string[];
    if (resource === "analysis-jobs") {
      if (rest[0] !== "articles" && rest[0] !== "topics") return;
      if (rest.length === 1) return { view: "list", resource, analysisType: rest[0] };
      id = rest[0] === "topics" ? `topic-analysis~${rest[1]}` : rest[1]; tail = rest.slice(2);
    } else if (resource === "topic-relations") {
      if (rest.length < 3) return;
      id = `${rest[0]}~${rest[2]}~${rest[1]}`; tail = rest.slice(3);
    } else { id = rest[0]; tail = rest.slice(1); }
    if (tail.length > 1) return;
    const action = tail[0] ?? "details";
    if (detailSections(resource).includes(action as DetailSection)) return { view: "detail", resource, id, section: action as DetailSection };
    if (content.has(resource) && (action === "edit" || action === "delete")) return { view: action, resource, id };
    if ((resource === "articles" || resource === "sources") && (action === "review" || (resource === "articles" && action === "classify") || (resource === "sources" && action === "fetch"))) return { view: "workflow", resource, id, action };
    return;
  }
}

export type RouteSearch = Record<string, string | string[] | undefined>;
export function routeSearchParams(search: RouteSearch): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) for (const entry of Array.isArray(value) ? value : value === undefined ? [] : [value]) params.append(key, entry);
  return params;
}

/** Keep old bookmarks, notifications and tab links usable without generating them anew. */
export function canonicalAdminRedirect(parts: string[], search: RouteSearch = {}): string | undefined {
  const query = routeSearchParams(search);
  let path = `/${parts.map(encodeURIComponent).join("/")}`;
  const resource = parts[0] as Resource;
  let changed = false;
  if (Object.hasOwn(resourcePaths, resource)) {
    changed = true;
    if (parts.length > 1 && (resource === "analysis-jobs" || resource === "topic-relations")) {
      if (resource === "topic-relations" && parts[1] === "new") {
        if (parts.length !== 2) return;
        path = `${resourceHref(resource)}/new`;
      }
      else {
        if (resource === "topic-relations" && (parts[1].split("~").length !== 3 || parts[1].split("~").some(part => !segment.test(part)))) return;
        path = recordHref(resource, { id: parts[1] }) + (parts.length > 2 ? `/${parts.slice(2).map(encodeURIComponent).join("/")}` : "");
      }
    } else path = resourceHref(resource) + (parts.length > 1 ? `/${parts.slice(1).map(encodeURIComponent).join("/")}` : "");
  }
  let route = resolveAdminRoute(path.slice(1).split("/").map(decodeURIComponent));
  if (!route) return;
  if (route.view === "list" && route.resource === "analysis-jobs" && query.has("analysis_type")) {
    const kind = query.get("analysis_type");
    if (kind === "articles" || kind === "topics") { path = resourceHref(route.resource, kind); query.delete("analysis_type"); changed = true; }
  }
  route = resolveAdminRoute(path.slice(1).split("/"));
  if (route?.view === "detail" && query.has("tab")) {
    const section = query.get("tab") as DetailSection;
    if (detailSections(route.resource).includes(section)) { path = recordHref(route.resource, { id: route.id }, section); query.delete("tab"); changed = true; }
  }
  if (route?.view === "detail" && path.endsWith("/details")) { path = path.slice(0, -"/details".length); changed = true; }
  return changed ? path + (query.size ? `?${query}` : "") : undefined;
}
