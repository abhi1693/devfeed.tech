"use client";
import { resourceHref, resourceTrail, type AnalysisType } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useTableQuery } from "@/lib/use-table-query";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { adminRouteTitle } from "@/lib/page-titles";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { TopicDiscovery } from "./topic-discovery";
import { TopicProposalsPanel } from "./topic-proposals";
import { RelationshipAddMenu } from "@/components/molecules/relationship-add-menu";
import { RecordTable } from "./record-table";
import { listRecords, type ListParams } from "@/lib/resource-api";
import { type Resource, resources, humanize } from "@/lib/resources";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { loadMatchingRows } from "@/lib/table-selection";
import { SourcePipeline } from "./source-pipeline";
import { ArticlePipeline } from "./article-pipeline";
export function ResourceList({
  resource,
  analysisType,
}: {
  resource: Resource;
  analysisType?: AnalysisType;
}) {
  const search = useTableQuery(resource);
  if (resource === "topics" && search.get("status") === "proposed") {
    search.set("view", "proposals");
    search.set("status", "pending");
    const sort = search.get("sort");
    if (sort === "name" || sort === "-name") search.set("sort", sort.replace("name", "slug"));
  }
  return resource === "articles" ? (
    <ArticlesScreen query={search.toString()} />
  ) : resource === "topics" ? (
    <TopicsScreen query={search.toString()} />
  ) : resource === "sources" ? (
    <SourcesScreen query={search.toString()} />
  ) : (
    <ResourceListTable resource={resource} analysisType={analysisType} query={search.toString()} />
  );
}

function SourcesScreen({ query }: { query: string }) {
  const search = new URLSearchParams(query);
  const pipeline = search.get("view") === "pipeline";
  function href(view: "list" | "pipeline") {
    const next = new URLSearchParams(search);
    next.set("view", view);
    next.delete("offset");
    for (const key of view === "list"
      ? ["stage", "source_type", "needs_attention", "terminal_since"]
      : ["approval_status", "enabled", "sort"])
      next.delete(key);
    return `/content/sources?${next}`;
  }
  return (
    <section className="min-w-0 space-y-6">
      <ResourceHeading title="Sources" trail={resourceTrail("sources")}>
        <Button variant="outline" size="sm" asChild>
          <Link href="/content/sources/import">Import</Link>
        </Button>
        <Button size="sm" asChild>
          <Link href="/content/sources/new" prefetch={false}>
            <Plus aria-hidden />
            Add source
          </Link>
        </Button>
      </ResourceHeading>
      <nav aria-label="Source views" className="flex gap-6 overflow-x-auto border-b">
        {(["list", "pipeline"] as const).map((view) => (
          <Link
            key={view}
            href={href(view)}
            prefetch={false}
            scroll={false}
            aria-current={(view === "pipeline") === pipeline ? "page" : undefined}
            className={`shrink-0 border-b-2 px-1 pb-3 text-sm ${(view === "pipeline") === pipeline ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >
            {view === "pipeline" ? "Pipeline" : "List"}
          </Link>
        ))}
      </nav>
      {pipeline ? (
        <SourcePipeline query={query} />
      ) : (
        <ResourceListTable resource="sources" query={query} embedded />
      )}
    </section>
  );
}

function ArticlesScreen({ query }: { query: string }) {
  const search = new URLSearchParams(query);
  const pipeline = search.get("view") === "pipeline";
  function href(view: "pipeline" | "list") {
    const params = new URLSearchParams(search);
    params.set("view", view);
    params.delete("offset");
    // View-specific filters must not silently narrow the other view.
    for (const key of view === "pipeline"
      ? ["review_status", "publication_status", "sort", "topic_id", "tag_id"]
      : ["needs_attention", "stage", "group_by"])
      params.delete(key);
    return `/content/articles?${params}`;
  }
  return (
    <section className="min-w-0 space-y-6">
      <ResourceHeading
        title="Articles"
        browserTitle={adminRouteTitle({ view: "list", resource: "articles" })}
        trail={resourceTrail("articles")}
      >
        <Button size="sm" asChild>
          <Link href="/content/articles/new">
            <Plus aria-hidden /> Add article
          </Link>
        </Button>
      </ResourceHeading>
      <nav aria-label="Article views" className="flex gap-6 border-b">
        {(["pipeline", "list"] as const).map((view) => (
          <Link
            key={view}
            href={href(view)}
            prefetch={false}
            scroll={false}
            aria-current={pipeline === (view === "pipeline") ? "page" : undefined}
            className={`border-b-2 px-1 pb-3 text-sm ${pipeline === (view === "pipeline") ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >
            {view === "pipeline" ? "Pipeline" : "List"}
          </Link>
        ))}
      </nav>
      {pipeline ? (
        <ArticlePipeline query={query} />
      ) : (
        <ResourceListTable resource="articles" query={query} embedded />
      )}
    </section>
  );
}

function TopicsScreen({ query }: { query: string }) {
  const [revision, setRevision] = useState(0);
  const search = new URLSearchParams(query);
  const proposals = search.get("view") === "proposals";
  const status = search.get("status") || "pending";
  function href(next: string) {
    const params = new URLSearchParams(search);
    params.set("offset", "0");
    if (next === "catalog") {
      for (const key of [
        "view",
        "status",
        "sort",
        "batch_id",
        "kind",
        "source",
        "action",
        "analysis",
        "missing",
      ])
        params.delete(key);
    } else {
      params.set("view", "proposals");
      params.set("status", next);
      if (!proposals) params.delete("sort");
    }
    return `/taxonomy/topics?${params}`;
  }
  return (
    <section className="min-w-0 space-y-6">
      <ResourceHeading
        title="Topics"
        trail={resourceTrail("topics")}
        description="Manage the topic catalog and track proposals from research to decision."
      >
        <Button variant="outline" size="sm" asChild>
          <Link href="/taxonomy/topics/import">Import</Link>
        </Button>
        <TopicDiscovery onComplete={() => setRevision((value) => value + 1)} />
        <Button size="sm" asChild>
          <Link href="/taxonomy/topics/new">
            <Plus aria-hidden />
            Add topic
          </Link>
        </Button>
      </ResourceHeading>
      <nav aria-label="Topic views" className="flex gap-6 overflow-x-auto border-b">
        {(
          [
            ["catalog", "Topics"],
            ["pending", "Proposed"],
            ["approved", "Approved"],
            ["rejected", "Rejected"],
          ] as const
        ).map(([value, label]) => {
          const selected = value === "catalog" ? !proposals : proposals && status === value;
          return (
            <Link
              key={value}
              href={href(value)}
              prefetch={false}
              scroll={false}
              aria-current={selected ? "page" : undefined}
              className={`shrink-0 border-b-2 px-1 pb-3 text-sm ${selected ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
            >
              {label}
            </Link>
          );
        })}
      </nav>
      {proposals ? (
        <TopicProposalsPanel query={query} refreshRevision={revision} />
      ) : (
        <ResourceListTable resource="topics" query={query} embedded refreshRevision={revision} />
      )}
    </section>
  );
}

function ResourceListTable({
  resource,
  analysisType,
  query,
  embedded = false,
  refreshRevision = 0,
}: {
  resource: Resource;
  analysisType?: AnalysisType;
  query: string;
  embedded?: boolean;
  refreshRevision?: number;
}) {
  const refreshSeconds = useRefreshInterval();
  const spec = resources[resource];
  const router = useRouter();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => {
      const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
      delete params.view;
      if (resource === "analysis-jobs") {
        delete params.analysis_type;
        if (analysisType) params.analysis_type = analysisType;
      }
      params.limit = Number(params.limit || 25);
      params.offset = Number(params.offset || 0);
      params.sort ||= spec.defaultSort;
      return listRecords(resource, params, signal);
    },
    [resource, query, spec.defaultSort, analysisType],
  );
  const { data, error, loading } = useRequest(
    `${resource}/${analysisType ?? "all"}?${query}/${revision}/${refreshRevision}`,
    load,
    refreshSeconds * 1000,
  );
  function change(values: Record<string, string>, replace = false) {
    const params = new URLSearchParams(query);
    for (const [key, value] of Object.entries(values)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const kind = Object.hasOwn(values, "analysis_type")
      ? (values.analysis_type as AnalysisType) || undefined
      : analysisType;
    params.delete("analysis_type");
    if (resource === "topics" && params.get("status") === "proposed") {
      params.set("status", "pending");
      params.set("view", "proposals");
      params.delete("sort");
    }
    router[replace ? "replace" : "push"](`${resourceHref(resource, kind)}?${params}`, {
      scroll: false,
    });
  }
  return (
    <section className="min-w-0 space-y-6">
      {!embedded && (
        <ResourceHeading
          trail={resourceTrail(resource)}
          title={spec.label}
          browserTitle={adminRouteTitle({ view: "list", resource, analysisType })}
          description={spec.description}
        >
          {resource === "sources" && (
            <Button variant="outline" size="sm" asChild>
              <Link href="/content/sources/import">Import</Link>
            </Button>
          )}
          {resource === "topic-relations" ? (
            <RelationshipAddMenu />
          ) : (
            !spec.readonly && (
              <Button size="sm" asChild>
                <Link href={`${resourceHref(resource)}/new`} prefetch={false}>
                  <Plus />
                  Add {spec.singular.toLowerCase()}
                </Link>
              </Button>
            )
          )}
        </ResourceHeading>
      )}
      <RecordTable
        toolbar={
          <div className="flex flex-wrap items-end gap-3">
            <SearchField
              className="max-w-lg flex-1"
              label={`Search ${spec.label.toLowerCase()}`}
              value={search.get("q") ?? ""}
              scopeKey={searchScope(query)}
              onSearch={(q) => change({ q, offset: "0" }, true)}
            />
            {spec.filter && (
              <Select
                className="w-full sm:w-48"
                label={spec.filter.label}
                value={search.get(spec.filter.key) ?? ""}
                onChange={(value) => change({ [spec.filter!.key]: value, offset: "0" })}
                placeholder={`All ${spec.filter.label.toLowerCase()}`}
                clearLabel={`All ${spec.filter.label.toLowerCase()}`}
                options={spec.filter.choices.map((choice) => ({
                  value: choice,
                  label: humanize(choice),
                }))}
              />
            )}
            {resource === "analysis-jobs" && (
              <Select
                className="w-full sm:w-48"
                label="Analysis type"
                value={analysisType ?? ""}
                onChange={(value) => change({ analysis_type: value, offset: "0" })}
                placeholder="Articles and topics"
                clearLabel="Articles and topics"
                options={[
                  { value: "articles", label: "Articles" },
                  { value: "topics", label: "Topics" },
                ]}
              />
            )}
            {([...search.keys()].some(
              (key) => !["limit", "offset", "sort", "view"].includes(key),
            ) ||
              analysisType) && (
              <Button
                variant="ghost"
                onClick={() => router.push(`${resourceHref(resource)}?offset=0`)}
              >
                Clear filters
              </Button>
            )}
          </div>
        }
        resource={resource}
        topicId={search.get("topic_id") || undefined}
        page={
          data ?? {
            items: [],
            total: 0,
            limit: Number(search.get("limit") || 25),
            offset: Number(search.get("offset") || 0),
          }
        }
        sort={search.get("sort") || spec.defaultSort}
        onChange={change}
        selectionKey={`${resource}/${analysisType ?? "all"}?${query}`}
        loadAllRows={(signal) =>
          loadMatchingRows(
            (offset, limit, signal) => {
              const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
              delete params.view;
              if (resource === "analysis-jobs") {
                delete params.analysis_type;
                if (analysisType) params.analysis_type = analysisType;
              }
              return listRecords(
                resource,
                { ...params, sort: params.sort || spec.defaultSort, offset, limit },
                signal,
              );
            },
            (row) => (resource === "analysis-jobs" ? `${row.kind}/${row.id}` : row.id),
            signal,
          )
        }
        loadFailedRows={(signal) =>
          loadMatchingRows(
            (offset, limit, signal) => {
              const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
              delete params.view;
              if (resource === "analysis-jobs") {
                delete params.analysis_type;
                if (analysisType) params.analysis_type = analysisType;
              }
              return listRecords(
                resource,
                {
                  ...params,
                  status: "failed",
                  retryable_only: "true",
                  sort: params.sort || spec.defaultSort,
                  offset,
                  limit,
                },
                signal,
              );
            },
            (row) => `${row.kind}/${row.id}`,
            signal,
          )
        }
        loading={loading}
        error={error}
        onRefresh={() => setRevision((value) => value + 1)}
      />
    </section>
  );
}
