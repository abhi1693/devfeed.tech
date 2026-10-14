import {
  resourceGroups,
  resourceKeys,
  resources,
  type Resource,
  type RouteGroup,
} from "./resources";
import { RelationOutRelation } from "./api/generated/models/relationOutRelation";
import type { RecordIdentity } from "./record-identities";

export type AnalysisType = "articles" | "topics";
export type UserSection =
  | "topics"
  | "sources"
  | "likes"
  | "bookmarks"
  | "reads"
  | "reading-days"
  | "interests"
  | "recommendations";
export type DetailSection =
  UserSection | "analysis" | "details" | "related" | "history" | "evidence" | "logs" | "relevance";
export type WorkflowAction = "review" | "classify" | "fetch";
const content = new Set<Resource>(["articles", "sources", "topics", "tags", "topic-relations"]);
const segment = /^[a-zA-Z0-9_-]+$/;

export function resourceHref(resource: Resource, analysisType?: AnalysisType): string {
  return (
    resources[resource].navigation.href +
    (resource === "analysis-jobs" && analysisType ? `/${analysisType}` : "")
  );
}

export function recordHref(identity: RecordIdentity, section: DetailSection = "details"): string {
  let path = resourceHref(identity.resource);
  if (identity.resource === "analysis-jobs") {
    path += `/${identity.kind === "topic-analysis" ? "topics" : "articles"}/${encodeURIComponent(identity.id)}`;
  } else if (identity.resource === "topic-relations") {
    path += `/${encodeURIComponent(identity.topicId)}/${identity.relation}/${encodeURIComponent(identity.relatedTopicId)}`;
  } else path += `/${encodeURIComponent(identity.id)}`;
  return path + (section === "details" ? "" : `/${section}`);
}

export function resourceTrail(resource: Resource) {
  return resources[resource].navigation.breadcrumbs.map(({ label, href }) => ({ label, href }));
}

export function detailSections(resource: Resource): DetailSection[] {
  if (resource === "users")
    return [
      "details",
      "analysis",
      "topics",
      "sources",
      "likes",
      "bookmarks",
      "reads",
      "reading-days",
      "interests",
      "recommendations",
    ];
  return [
    "details",
    ...(resource === "sources" ? ["relevance" as const] : []),
    ...(content.has(resource) ? ["related" as const] : ["logs" as const]),
    ...(["articles", "sources"].includes(resource) ? ["history" as const] : []),
    ...(resource === "articles" ? ["evidence" as const] : []),
  ];
}

export type AdminRoute =
  | { view: "group"; group: RouteGroup }
  | { view: "list"; resource: Resource; analysisType?: AnalysisType }
  | { view: "new"; resource: Resource }
  | { view: "detail"; identity: RecordIdentity; section: DetailSection }
  | { view: "edit" | "delete"; identity: RecordIdentity }
  | {
      view: "workflow";
      identity: Extract<RecordIdentity, { resource: "articles" | "sources" }>;
      action: WorkflowAction;
    }
  | { view: "relationship-discover" | "relationship-proposals" }
  | { view: "relationship-proposal"; id: string }
  | { view: "import" }
  | { view: "source-import" }
  | { view: "source-import-review"; id: string }
  | { view: "proposal" | "enrich"; id: string };

/** Decode browser paths once; record identities retain their native fields. */
export function resolveAdminRoute(parts: string[]): AdminRoute | undefined {
  if (parts.some((part) => !segment.test(part))) return;
  const path = `/${parts.join("/")}`;
  for (const [group, definition] of Object.entries(resourceGroups))
    if (path === definition.href) return { view: "group", group: group as RouteGroup };
  for (const resource of resourceKeys) {
    const prefix = resources[resource].navigation.href;
    if (path !== prefix && !path.startsWith(`${prefix}/`)) continue;
    const rest = parts.slice(prefix.split("/").length - 1);
    if (!rest.length) return { view: "list", resource };
    if (resource === "sources") {
      if (rest.length === 1 && rest[0] === "import") return { view: "source-import" };
      if (rest.length === 2 && rest[0] === "imports")
        return { view: "source-import-review", id: rest[1] };
    }
    if (resource === "topics") {
      if (rest.length === 1 && rest[0] === "import") return { view: "import" };
      if (rest[0] === "proposals")
        return rest.length === 2 ? { view: "proposal", id: rest[1] } : undefined;
      if (rest.length === 2 && rest[1] === "enrich") return { view: "enrich", id: rest[0] };
    }
    if (resource === "topic-relations") {
      if (rest.length === 1 && rest[0] === "discover") return { view: "relationship-discover" };
      if (rest[0] === "proposals")
        return rest.length === 1
          ? { view: "relationship-proposals" }
          : rest.length === 2
            ? { view: "relationship-proposal", id: rest[1] }
            : undefined;
    }
    if (rest.length === 1 && rest[0] === "new")
      return content.has(resource) ? { view: "new", resource } : undefined;
    let identity: RecordIdentity;
    let tail: string[];
    if (resource === "analysis-jobs") {
      if (rest[0] !== "articles" && rest[0] !== "topics") return;
      if (rest.length === 1) return { view: "list", resource, analysisType: rest[0] };
      identity = {
        resource,
        kind: rest[0] === "topics" ? "topic-analysis" : "analysis",
        id: rest[1],
      };
      tail = rest.slice(2);
    } else if (resource === "topic-relations") {
      if (rest.length < 3) return;
      if (!Object.hasOwn(RelationOutRelation, rest[1])) return;
      identity = {
        resource,
        topicId: rest[0],
        relatedTopicId: rest[2],
        relation: rest[1] as RelationOutRelation,
      };
      tail = rest.slice(3);
    } else {
      identity = { resource, id: rest[0] };
      tail = rest.slice(1);
    }
    if (tail.length > 1) return;
    const action = tail[0] ?? "details";
    if (detailSections(resource).includes(action as DetailSection))
      return { view: "detail", identity, section: action as DetailSection };
    if (content.has(resource) && (action === "edit" || action === "delete"))
      return { view: action, identity };
    if (
      (identity.resource === "articles" || identity.resource === "sources") &&
      (action === "review" ||
        (identity.resource === "articles" && action === "classify") ||
        (identity.resource === "sources" && action === "fetch"))
    )
      return { view: "workflow", identity, action };
    return;
  }
}

export type RouteSearch = Record<string, string | string[] | undefined>;
export function routeSearchParams(search: RouteSearch): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search))
    for (const entry of Array.isArray(value) ? value : value === undefined ? [] : [value])
      params.append(key, entry);
  return params;
}

/** Decode retired URL tokens only at the bookmark redirect boundary. */
export function canonicalAdminRedirect(
  parts: string[],
  search: RouteSearch = {},
): string | undefined {
  const query = routeSearchParams(search);
  const resource = parts[0] as Resource;
  let normalized = parts;
  let changed = false;
  if (
    Object.hasOwn(resources, resource) &&
    resources[resource].navigation.href !== `/${resource}`
  ) {
    changed = true;
    const prefix = resources[resource].navigation.href.slice(1).split("/");
    let rest = parts.slice(1);
    if (resource === "analysis-jobs" && rest.length) {
      const oldId = rest[0];
      const topic = oldId.startsWith("topic-analysis~");
      const id = topic ? oldId.slice("topic-analysis~".length) : oldId;
      rest = [topic ? "topics" : "articles", id, ...rest.slice(1)];
    } else if (resource === "topic-relations" && rest.length && rest[0] !== "new") {
      const [from, to, relation, extra] = rest[0].split("~");
      if (!from || !to || !relation || extra !== undefined) return;
      rest = [from, relation, to, ...rest.slice(1)];
    }
    normalized = [...prefix, ...rest];
  }
  const route = resolveAdminRoute(normalized);
  if (!route) return;
  let path = `/${normalized.map(encodeURIComponent).join("/")}`;
  if (route.view === "list" && route.resource === "analysis-jobs" && query.has("analysis_type")) {
    const kind = query.get("analysis_type");
    if (kind === "articles" || kind === "topics") {
      path = resourceHref(route.resource, kind);
      query.delete("analysis_type");
      changed = true;
    }
  }
  if (route.view === "detail") {
    let section = route.section;
    if (query.has("tab")) {
      const tab = query.get("tab") as DetailSection;
      if (detailSections(route.identity.resource).includes(tab)) {
        section = tab;
        query.delete("tab");
        changed = true;
      }
    }
    if (normalized.at(-1) === "details") changed = true;
    path = recordHref(route.identity, section);
  }
  return changed ? path + (query.size ? `?${query}` : "") : undefined;
}
