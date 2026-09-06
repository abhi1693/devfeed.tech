"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { RequestState } from "@/components/molecules/request-state";
import { listRecords, type ListParams } from "@/lib/resource-api";
import { type Resource, resources } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { RecordTable } from "./record-table";
export function RelatedRecords({ resource, filter, title }: { resource: Resource; filter: Record<string, string>; title?: string }) {
  const [page, setPage] = useState<ListParams>({ limit: 10, offset: 0, sort: resources[resource].defaultSort });
  const serialized = JSON.stringify({ ...filter, ...page });
  const load = useCallback((signal: AbortSignal) => listRecords(resource, JSON.parse(serialized), signal), [resource, serialized]);
  const result = useRequest(`${resource}/${serialized}`, load);
  return <section className="space-y-3"><div className="flex items-center justify-between"><h2 className="font-semibold">{title ?? resources[resource].label}{result.data ? ` (${result.data.total})` : ""}</h2><Link prefetch={false} href={`/${resource}?${new URLSearchParams(filter)}`} className="text-xs text-blue-700 hover:underline">View full list</Link></div><RequestState loading={result.loading} error={result.error} />{result.data && <RecordTable resource={resource} page={result.data} sort={String(page.sort)} onChange={changes => setPage(current => ({ ...current, ...changes, limit: Number(changes.limit ?? current.limit), offset: Number(changes.offset ?? current.offset) }))} />}</section>;
}
