import { describe, expect, it } from "vitest";
import { resourceKeys, resources, type Resource } from "@/lib/resources";
import type { RecordIdentity } from "@/lib/record-identities";
import { RelationOutRelation } from "@/lib/api/generated/models/relationOutRelation";
import { adminRouteTitle } from "@/lib/page-titles";
import {
  canonicalAdminRedirect,
  detailSections,
  recordHref,
  resolveAdminRoute,
  resourceHref,
  type AdminRoute,
} from "@/lib/routes";

const parse = (path: string) => resolveAdminRoute(path.slice(1).split("/"));
const identityFor = (resource: Resource): RecordIdentity => {
  if (resource === "topic-relations")
    return {
      resource,
      topicId: "from-topic",
      relatedTopicId: "to-topic",
      relation: "related_to",
    };
  if (resource === "analysis-jobs") return { resource, kind: "analysis", id: "record-1" };
  return { resource, id: "record-1" };
};
const redirect = (path: string) => {
  const url = new URL(path, "https://admin.example");
  const search: Record<string, string[]> = {};
  for (const key of url.searchParams.keys()) search[key] = url.searchParams.getAll(key);
  return canonicalAdminRedirect(url.pathname.slice(1).split("/").map(decodeURIComponent), search);
};

describe("shared browser routes", () => {
  it("places source relevance immediately after details and restricts it to sources", () => {
    expect(detailSections("sources")).toEqual(["details", "relevance", "related", "history"]);
    expect(parse("/content/sources/source-1/relevance")).toEqual({
      view: "detail",
      identity: { resource: "sources", id: "source-1" },
      section: "relevance",
    });
    expect(parse("/content/articles/article-1/relevance")).toBeUndefined();
  });
  it.each(resourceKeys)("resolves %s list, records, sections, and supported forms", (resource) => {
    const base = resourceHref(resource);
    const identity = identityFor(resource);
    const href = recordHref(identity);
    expect(parse(base)).toEqual({ view: "list", resource });
    expect(parse(href)).toEqual({ view: "detail", identity, section: "details" });
    expect(redirect(base)).toBeUndefined();
    for (const section of detailSections(resource)) {
      expect(parse(recordHref(identity, section))).toEqual({
        view: "detail",
        identity,
        section,
      });
    }
    if (!resources[resource].readonly) {
      expect(parse(`${base}/new`)).toEqual({ view: "new", resource });
      for (const view of ["edit", "delete"])
        expect(parse(`${href}/${view}`)).toEqual({ view, identity });
    } else {
      expect(parse(`${base}/new`)).toBeUndefined();
      expect(parse(`${href}/edit`)).toBeUndefined();
      expect(parse(`${href}/delete`)).toBeUndefined();
    }
    expect(parse(`${href}/extra/segments`)).toBeUndefined();
  });

  it.each(["articles", "topics"] as const)(
    "keeps %s analysis scope in list and run URLs",
    (kind) => {
      const base = resourceHref("analysis-jobs", kind);
      const identity: RecordIdentity = {
        resource: "analysis-jobs",
        id: "run-1",
        kind: kind === "topics" ? "topic-analysis" : "analysis",
      };
      expect(parse(base)).toEqual({ view: "list", resource: "analysis-jobs", analysisType: kind });
      expect(recordHref(identity)).toBe(`${base}/run-1`);
      expect(parse(`${base}/run-1/logs`)).toEqual({
        view: "detail",
        identity,
        section: "logs",
      });
    },
  );

  it.each([
    ["/content", { view: "group", group: "content" }],
    ["/taxonomy", { view: "group", group: "taxonomy" }],
    ["/jobs", { view: "group", group: "jobs" }],
    ["/jobs/enrichment", { view: "group", group: "enrichment" }],
    ["/taxonomy/topics/import", { view: "import" }],
    ["/taxonomy/topics/proposals/proposal-1", { view: "proposal", id: "proposal-1" }],
    ["/taxonomy/topics/topic-1/enrich", { view: "enrich", id: "topic-1" }],
    [
      "/content/articles/article-1/review",
      { view: "workflow", identity: { resource: "articles", id: "article-1" }, action: "review" },
    ],
    [
      "/content/articles/article-1/classify",
      { view: "workflow", identity: { resource: "articles", id: "article-1" }, action: "classify" },
    ],
    [
      "/content/sources/source-1/fetch",
      { view: "workflow", identity: { resource: "sources", id: "source-1" }, action: "fetch" },
    ],
  ])("resolves workflow %s", (path, expected) => expect(parse(String(path))).toEqual(expected));

  it("represents relationship direction in separate path segments", () => {
    expect(
      recordHref({
        resource: "topic-relations",
        topicId: "react",
        relatedTopicId: "javascript",
        relation: "uses_language",
      }),
    ).toBe("/taxonomy/relationships/react/uses_language/javascript");
  });

  it.each(Object.values(RelationOutRelation))("preserves %s relationship identity", (relation) => {
    const identity: RecordIdentity = {
      resource: "topic-relations",
      topicId: "from-1",
      relatedTopicId: "to-2",
      relation,
    };
    expect(parse(recordHref(identity))).toEqual({ view: "detail", identity, section: "details" });
    expect(parse(`${recordHref(identity)}/edit`)).toEqual({ view: "edit", identity });
    expect(parse(`${recordHref(identity)}/delete`)).toEqual({ view: "delete", identity });
    expect(parse(recordHref({ ...identity, topicId: "to-2", relatedTopicId: "from-1" }))).toEqual({
      view: "detail",
      identity: { ...identity, topicId: "to-2", relatedTopicId: "from-1" },
      section: "details",
    });
  });

  it("keeps identical analysis run IDs distinct through routes and titles", () => {
    const article: RecordIdentity = {
      resource: "analysis-jobs",
      kind: "analysis",
      id: "same-run-123",
    };
    const topic: RecordIdentity = { ...article, kind: "topic-analysis" };
    const articleRoute = resolveAdminRoute(recordHref(article).slice(1).split("/"))!;
    const topicRoute = resolveAdminRoute(recordHref(topic).slice(1).split("/"))!;
    expect(articleRoute).toEqual({ view: "detail", identity: article, section: "details" });
    expect(topicRoute).toEqual({ view: "detail", identity: topic, section: "details" });
    expect(adminRouteTitle(articleRoute)).toBe("Run same-run · Article AI analysis");
    expect(adminRouteTitle(topicRoute)).toBe("Run same-run · Topic AI analysis");
  });

  it("titles structured record identities without interpreting prefixes or delimiters", () => {
    expect(
      adminRouteTitle({
        view: "detail",
        identity: { resource: "analysis-jobs", kind: "analysis", id: "topic-analysis~opaque" },
        section: "details",
      }),
    ).toBe("Run topic-an · Article AI analysis");
    expect(
      adminRouteTitle(
        {
          view: "detail",
          identity: {
            resource: "topic-relations",
            topicId: "react",
            relatedTopicId: "javascript",
            relation: "uses_language",
          },
          section: "related",
        },
        "React uses JavaScript",
      ),
    ).toBe("Related objects · Uses language · Topic relationship");
    expect(
      adminRouteTitle(
        {
          view: "workflow",
          identity: { resource: "articles", id: "article-1" },
          action: "classify",
        },
        "Article title",
      ),
    ).toBe("Classify Article title · Article");
  });

  it("titles relationships by their native relation without exposing a row key", () => {
    expect(
      adminRouteTitle({
        view: "detail",
        identity: {
          resource: "topic-relations",
          topicId: "react",
          relatedTopicId: "javascript",
          relation: "uses_language",
        },
        section: "details",
      }),
    ).toBe("Uses language · Topic relationship");
  });

  it.each([
    ["detail", "Uses language · Topic relationship"],
    ["edit", "Edit Uses language · Topic relationship"],
    ["delete", "Delete Uses language · Topic relationship"],
  ] as const)("keeps loaded %s relationship titles human-readable", (view, title) => {
    const identity: RecordIdentity = {
      resource: "topic-relations",
      topicId: "react",
      relatedTopicId: "javascript",
      relation: "uses_language",
    };
    const route: AdminRoute =
      view === "detail" ? { view, identity, section: "details" } : { view, identity };
    expect(adminRouteTitle(route, "uses_language")).toBe(title);
    expect(adminRouteTitle(route, '["topic-relations","react","uses_language","javascript"]')).toBe(
      title,
    );
  });

  it.each([
    "/taxonomy/topics/proposals",
    "/unknown",
    "/content/unknown",
    "/taxonomy/tags/tag/import",
    "/taxonomy/topics/import/extra",
    "/taxonomy/topics/proposals/p/extra",
    "/jobs/analysis/raw-id",
    "/jobs/ingestion/run/review",
    "/content/sources/source/classify",
    "/content/articles/article/fetch",
    "/taxonomy/relationships/a/b",
    "/taxonomy/relationships/a/unknown/b",
    "/taxonomy/relationships/a/constructor/b",
    "/taxonomy/relationships/a/toString/b",
    "/taxonomy/relationships/a/uses_language/..",
    "/taxonomy/relationships/a~b/uses_language/c",
    "/jobs/analysis/topics/topic-analysis~run-1",
    "/jobs/analysis/topics/run-1/extra/segments",
    "/content/articles/..",
    "/content/articles/a%2fb",
  ])("rejects unsupported route %s", (path) => expect(parse(path)).toBeUndefined());
});

describe("old bookmark compatibility", () => {
  it.each([
    "/taxonomy/topics?status=active",
    "/taxonomy/topics?status=rejected",
    "/taxonomy/topics?status=pending&view=proposals",
    "/taxonomy/topics/topic-1?status=proposed",
  ])("does not redirect unrelated topic views %s", (path) =>
    expect(redirect(path)).toBeUndefined(),
  );
  it.each(resourceKeys.filter((resource) => resourceHref(resource) !== `/${resource}`))(
    "redirects %s list and record with filters intact",
    (resource) => {
      const query = "?q=react&status=running&sort=-created_at&offset=25&limit=25&batch_id=batch-1";
      expect(redirect(`/${resource}${query}`)).toBe(`${resourceHref(resource)}${query}`);
      const identity = identityFor(resource);
      const oldId = resource === "topic-relations" ? "from-topic~to-topic~related_to" : "record-1";
      expect(redirect(`/${resource}/${oldId}${query}`)).toBe(`${recordHref(identity)}${query}`);
    },
  );
  it.each([
    ["/topics/proposals/proposal-1", "/taxonomy/topics/proposals/proposal-1"],
    ["/topics/import", "/taxonomy/topics/import"],
    ["/topics/topic-1/enrich", "/taxonomy/topics/topic-1/enrich"],
    ["/sources/source-1/fetch", "/content/sources/source-1/fetch"],
    [
      "/analysis-jobs?analysis_type=topics&status=running&offset=25",
      "/jobs/analysis/topics?status=running&offset=25",
    ],
    ["/jobs/analysis?analysis_type=articles", "/jobs/analysis/articles"],
    [
      "/analysis-jobs/topic-analysis~run-1?tab=logs&attempt=2",
      "/jobs/analysis/topics/run-1/logs?attempt=2",
    ],
    ["/analysis-jobs/run-1?tab=logs", "/jobs/analysis/articles/run-1/logs"],
    ["/articles/article-1?tab=history", "/content/articles/article-1/history"],
    ["/content/articles/article-1?tab=evidence", "/content/articles/article-1/evidence"],
    ["/jobs/analysis/topics/run-1/details", "/jobs/analysis/topics/run-1"],
    [
      "/topic-relations/from~to~uses_language/edit",
      "/taxonomy/relationships/from/uses_language/to/edit",
    ],
    ["/topics?q=hello+world&q=second", "/taxonomy/topics?q=hello+world&q=second"],
  ])("redirects %s without looping", (old, canonical) => {
    expect(redirect(old)).toBe(canonical);
    expect(redirect(canonical)).toBeUndefined();
  });
  it.each([
    "/taxonomy/topics/proposals",
    "/topics/proposals?status=pending",
    "/unknown",
    "/api/v1/admin/topics",
    "/login",
    "/topic-relations/malformed",
    "/topic-relations/from~to~unknown",
    "/topic-relations/from~to~constructor",
    "/topic-relations/from~to~uses_language~extra",
    "/topic-relations/from~~uses_language",
    "/analysis-jobs/topic-analysis~",
    "/topic-relations/new/extra",
    "/topics/id/edit/extra",
    "/analysis-jobs/run-1/delete",
  ])("does not redirect unsupported %s", (path) => expect(redirect(path)).toBeUndefined());
  it("does not turn user input into a redirect to another origin", () => {
    for (const resource of resourceKeys)
      expect(canonicalAdminRedirect([resource, "//external.example"])).toBeUndefined();
  });
});
