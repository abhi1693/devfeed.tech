import { describe, expect, it } from "vitest";
import { resourceKeys, resources } from "@/lib/resources";
import { canonicalAdminRedirect, detailSections, recordHref, resolveAdminRoute, resourceHref } from "@/lib/routes";

const parse = (path: string) => resolveAdminRoute(path.slice(1).split("/"));
const redirect = (path: string) => {
  const url = new URL(path, "https://admin.example");
  const search: Record<string, string[]> = {};
  for (const key of url.searchParams.keys()) search[key] = url.searchParams.getAll(key);
  return canonicalAdminRedirect(url.pathname.slice(1).split("/").map(decodeURIComponent), search);
};

describe("shared browser routes", () => {
  it.each(resourceKeys)("resolves %s list, records, sections, and supported forms", resource => {
    const base = resourceHref(resource);
    const id = resource === "topic-relations" ? "from-topic~to-topic~related_to" : "record-1";
    const href = recordHref(resource, { id });
    expect(parse(base)).toEqual({ view: "list", resource });
    expect(parse(href)).toEqual({ view: "detail", resource, id, section: "details" });
    expect(redirect(base)).toBeUndefined();
    for (const section of detailSections(resource)) {
      expect(parse(recordHref(resource, { id }, section))).toEqual({ view: "detail", resource, id, section });
    }
    if (!resources[resource].readonly) {
      expect(parse(`${base}/new`)).toEqual({ view: "new", resource });
      for (const view of ["edit", "delete"]) expect(parse(`${href}/${view}`)).toEqual({ view, resource, id });
    } else {
      expect(parse(`${base}/new`)).toBeUndefined();
      expect(parse(`${href}/edit`)).toBeUndefined();
      expect(parse(`${href}/delete`)).toBeUndefined();
    }
    expect(parse(`${href}/extra/segments`)).toBeUndefined();
  });

  it.each(["articles", "topics"] as const)("keeps %s analysis scope in list and run URLs", kind => {
    const base = resourceHref("analysis-jobs", kind);
    const record = { id: "run-1", kind: kind === "topics" ? "topic-analysis" : "analysis" };
    expect(parse(base)).toEqual({ view: "list", resource: "analysis-jobs", analysisType: kind });
    expect(recordHref("analysis-jobs", record)).toBe(`${base}/run-1`);
    expect(parse(`${base}/run-1/logs`)).toEqual({ view: "detail", resource: "analysis-jobs", id: kind === "topics" ? "topic-analysis~run-1" : "run-1", section: "logs" });
  });

  it.each([
    ["/content", { view: "group", group: "content" }],
    ["/taxonomy", { view: "group", group: "taxonomy" }],
    ["/jobs", { view: "group", group: "jobs" }],
    ["/jobs/enrichment", { view: "group", group: "enrichment" }],
    ["/taxonomy/topics/import", { view: "import" }],
    ["/taxonomy/topics/proposals/proposal-1", { view: "proposal", id: "proposal-1" }],
    ["/taxonomy/topics/topic-1/enrich", { view: "enrich", id: "topic-1" }],
    ["/content/articles/article-1/review", { view: "workflow", resource: "articles", id: "article-1", action: "review" }],
    ["/content/articles/article-1/classify", { view: "workflow", resource: "articles", id: "article-1", action: "classify" }],
    ["/content/sources/source-1/fetch", { view: "workflow", resource: "sources", id: "source-1", action: "fetch" }],
  ])("resolves workflow %s", (path, expected) => expect(parse(String(path))).toEqual(expected));

  it("represents relationship direction in separate path segments", () => {
    expect(recordHref("topic-relations", { id: "react~javascript~uses_language" })).toBe("/taxonomy/relationships/react/uses_language/javascript");
  });

  it.each(["/taxonomy/topics/proposals", "/unknown", "/content/unknown", "/taxonomy/tags/tag/import", "/taxonomy/topics/import/extra", "/taxonomy/topics/proposals/p/extra", "/jobs/analysis/raw-id", "/jobs/ingestion/run/review", "/content/sources/source/classify", "/content/articles/article/fetch", "/taxonomy/relationships/a/b", "/content/articles/..", "/content/articles/a%2fb"])("rejects unsupported route %s", path => expect(parse(path)).toBeUndefined());
});

describe("old bookmark compatibility", () => {
  it.each(["/taxonomy/topics?status=active", "/taxonomy/topics?status=rejected", "/taxonomy/topics?status=pending&view=proposals", "/taxonomy/topics/topic-1?status=proposed"])("does not redirect unrelated topic views %s", path => expect(redirect(path)).toBeUndefined());
  it.each(resourceKeys)("redirects %s list and record with filters intact", resource => {
    const query = "?q=react&status=running&sort=-created_at&offset=25&limit=25&batch_id=batch-1";
    expect(redirect(`/${resource}${query}`)).toBe(`${resourceHref(resource)}${query}`);
    const id = resource === "topic-relations" ? "from~to~related_to" : "record-1";
    expect(redirect(`/${resource}/${id}${query}`)).toBe(`${recordHref(resource, { id })}${query}`);
  });
  it.each([
    ["/topics/proposals/proposal-1", "/taxonomy/topics/proposals/proposal-1"],
    ["/topics/import", "/taxonomy/topics/import"],
    ["/topics/topic-1/enrich", "/taxonomy/topics/topic-1/enrich"],
    ["/sources/source-1/fetch", "/content/sources/source-1/fetch"],
    ["/analysis-jobs?analysis_type=topics&status=running&offset=25", "/jobs/analysis/topics?status=running&offset=25"],
    ["/jobs/analysis?analysis_type=articles", "/jobs/analysis/articles"],
    ["/analysis-jobs/topic-analysis~run-1?tab=logs&attempt=2", "/jobs/analysis/topics/run-1/logs?attempt=2"],
    ["/analysis-jobs/run-1?tab=logs", "/jobs/analysis/articles/run-1/logs"],
    ["/articles/article-1?tab=history", "/content/articles/article-1/history"],
    ["/content/articles/article-1?tab=evidence", "/content/articles/article-1/evidence"],
    ["/jobs/analysis/topics/run-1/details", "/jobs/analysis/topics/run-1"],
    ["/topic-relations/from~to~uses_language/edit", "/taxonomy/relationships/from/uses_language/to/edit"],
    ["/topics?q=hello+world&q=second", "/taxonomy/topics?q=hello+world&q=second"],
  ])("redirects %s without looping", (old, canonical) => {
    expect(redirect(old)).toBe(canonical);
    expect(redirect(canonical)).toBeUndefined();
  });
  it.each(["/taxonomy/topics/proposals", "/topics/proposals?status=pending", "/unknown", "/api/v1/admin/topics", "/login", "/topic-relations/malformed", "/topic-relations/new/extra", "/topics/id/edit/extra", "/analysis-jobs/run-1/delete"])("does not redirect unsupported %s", path => expect(redirect(path)).toBeUndefined());
  it("does not turn user input into a redirect to another origin", () => {
    for (const resource of resourceKeys) expect(canonicalAdminRedirect([resource, "//external.example"])).toBeUndefined();
  });
});
