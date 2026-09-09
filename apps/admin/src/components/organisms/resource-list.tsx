"use client";
import { resourceHref, resourceTrail, type AnalysisType } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Select } from "@/components/molecules/select";
import { PageHeading } from "@/components/molecules/page-heading";
import { TopicAddMenu } from "@/components/molecules/topic-add-menu";
import { RecordTable } from "./record-table";
import { listRecords, type ListParams } from "@/lib/resource-api";
import { type Resource, resources, humanize } from "@/lib/resources";
import { RefreshInterval } from "@/components/molecules/refresh-interval";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { loadMatchingRows } from "@/lib/table-selection";
export function ResourceList({ resource, analysisType }: { resource: Resource; analysisType?: AnalysisType }) {
  const [refreshSeconds, setRefreshSeconds] = useRefreshInterval();
  const spec = resources[resource];
  const router = useRouter(); const search = useSearchParams(); const query = search.toString();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => {
    const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
    if (resource === "analysis-jobs") { delete params.analysis_type; if (analysisType) params.analysis_type = analysisType; }
    params.limit = Number(params.limit || 25); params.offset = Number(params.offset || 0); params.sort ||= spec.defaultSort;
    return listRecords(resource, params, signal);
  }, [resource, query, spec.defaultSort, analysisType]);
  const { data, error, loading, refreshing } = useRequest(`${resource}/${analysisType ?? "all"}?${query}/${revision}`, load, refreshSeconds * 1000);
  function change(values: Record<string, string>) { const params = new URLSearchParams(query); for (const [key, value] of Object.entries(values)) { if (value) params.set(key, value); else params.delete(key); } const kind = Object.hasOwn(values, "analysis_type") ? values.analysis_type as AnalysisType || undefined : analysisType; params.delete("analysis_type"); router.push(`${resourceHref(resource, kind)}?${params}`, { scroll: false }); }
  return <section className="min-w-0 space-y-6">
    <PageHeading trail={resourceTrail(resource)} title={spec.label} description={spec.description}>
      {resource === "topics" && <Button variant="outline" size="sm" asChild><Link href="/taxonomy/topics/import">Import</Link></Button>}
      <RefreshInterval value={refreshSeconds} onChange={setRefreshSeconds} loading={loading || refreshing} />
      {resource === "topics" || resource === "topic-relations" ? <TopicAddMenu relationships={resource === "topic-relations"} /> : !spec.readonly && <Button size="sm" asChild><Link href={`${resourceHref(resource)}/new`} prefetch={false}><Plus />Add {spec.singular.toLowerCase()}</Link></Button>}
    </PageHeading>
    <div className="flex flex-wrap items-end gap-3"><form key={query} className="flex max-w-lg flex-1 gap-2" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); change({ q: String(form.get("q") || ""), offset: "0" }); }}><Input aria-label={`Search ${spec.label.toLowerCase()}`} name="q" placeholder={`Search ${spec.label.toLowerCase()}…`} defaultValue={search.get("q") ?? ""} maxLength={200} /><Button variant="outline" type="submit">Search</Button></form>
      {spec.filter && <Select className="w-full sm:w-48" label={spec.filter.label} value={search.get(spec.filter.key) ?? ""} onChange={value => change({ [spec.filter!.key]: value, offset: "0" })} placeholder={`All ${spec.filter.label.toLowerCase()}`} clearLabel={`All ${spec.filter.label.toLowerCase()}`} options={spec.filter.choices.map(choice => ({ value: choice, label: humanize(choice) }))} />}
      {resource === "analysis-jobs" && <Select className="w-full sm:w-48" label="Analysis type" value={analysisType ?? ""} onChange={value => change({ analysis_type: value, offset: "0" })}
        placeholder="Articles and topics" clearLabel="Articles and topics" options={[{ value: "articles", label: "Articles" }, { value: "topics", label: "Topics" }]} />}
      {(query || analysisType) && <Button variant="ghost" onClick={() => router.push(resourceHref(resource))}>Clear filters</Button>}
    </div>
    <RecordTable resource={resource} page={data ?? { items: [], total: 0, limit: Number(search.get("limit") || 25), offset: Number(search.get("offset") || 0) }}
      sort={search.get("sort") || spec.defaultSort} onChange={change} selectionKey={`${resource}/${analysisType ?? "all"}?${query}`}
      loadAllRows={signal => loadMatchingRows((offset, limit, signal) => {
        const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
        if (resource === "analysis-jobs") { delete params.analysis_type; if (analysisType) params.analysis_type = analysisType; }
        return listRecords(resource, { ...params, sort: params.sort || spec.defaultSort, offset, limit }, signal);
      }, row => resource === "analysis-jobs" ? `${row.kind}/${row.id}` : row.id, signal)}
      loading={loading} error={error} onRefresh={() => setRevision(value => value + 1)} />
  </section>;
}
