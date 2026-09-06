"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, RotateCw } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Combobox } from "@/components/molecules/combobox";
import { PageHeading } from "@/components/molecules/page-heading";
import { RequestState } from "@/components/molecules/request-state";
import { RecordTable } from "./record-table";
import { listRecords, type ListParams } from "@/lib/resource-api";
import { type Resource, resources, humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
export function ResourceList({ resource }: { resource: Resource }) {
  const spec = resources[resource];
  const router = useRouter(); const search = useSearchParams(); const query = search.toString();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => {
    const params = Object.fromEntries(new URLSearchParams(query)) as ListParams;
    params.limit = Number(params.limit || 25); params.offset = Number(params.offset || 0); params.sort ||= spec.defaultSort;
    return listRecords(resource, params, signal);
  }, [resource, query, spec.defaultSort]);
  const { data, error, loading } = useRequest(`${resource}?${query}/${revision}`, load);
  function change(values: Record<string, string>) { const params = new URLSearchParams(query); for (const [key, value] of Object.entries(values)) { if (value) params.set(key, value); else params.delete(key); } router.push(`/${resource}?${params}`, { scroll: false }); }
  return <section className="space-y-6">
    <PageHeading title={spec.label} description={spec.description}><Button variant="outline" size="sm" onClick={() => setRevision(value => value + 1)} loading={loading} loadingText="Refresh"><RotateCw />Refresh</Button>{!spec.readonly && <Button size="sm" asChild><Link href={`/${resource}/new`} prefetch={false}><Plus />Add {spec.singular.toLowerCase()}</Link></Button>}</PageHeading>
    <div className="flex flex-wrap items-end gap-3"><form key={query} className="flex max-w-lg flex-1 gap-2" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); change({ q: String(form.get("q") || ""), offset: "0" }); }}><Input aria-label={`Search ${spec.label.toLowerCase()}`} name="q" placeholder={`Search ${spec.label.toLowerCase()}…`} defaultValue={search.get("q") ?? ""} maxLength={200} /><Button variant="outline" type="submit">Search</Button></form>
      {spec.filter && <Combobox className="w-full sm:w-48" label={spec.filter.label} value={search.get(spec.filter.key) ?? ""} onChange={value => change({ [spec.filter!.key]: value, offset: "0" })} placeholder={`All ${spec.filter.label.toLowerCase()}`} clearLabel={`All ${spec.filter.label.toLowerCase()}`} options={spec.filter.choices.map(choice => ({ value: choice, label: humanize(choice) }))} />}
      {query && <Button variant="ghost" onClick={() => router.push(`/${resource}`)}>Clear filters</Button>}
    </div>
    <RequestState loading={loading} error={error} retry={() => setRevision(value => value + 1)} />
    {data && <RecordTable resource={resource} page={data} sort={search.get("sort") || spec.defaultSort} onChange={change} />}
  </section>;
}
