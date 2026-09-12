"use client";
import { useSettings } from "@/lib/use-settings";
import { resourceHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useState } from "react";
import { listRecords, type ListParams } from "@/lib/resource-api";
import { type Resource, resources } from "@/lib/resources";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { RecordTable } from "./record-table";
import { loadMatchingRows } from "@/lib/table-selection";
export function RelatedRecords({
  resource,
  filter,
  title,
}: {
  resource: Resource;
  filter: Record<string, string>;
  title?: string;
}) {
  const refreshSeconds = useRefreshInterval();
  const { settings } = useSettings();
  const [page, setPage] = useState<ListParams>({
    limit: settings.defaults.page_size,
    offset: 0,
    sort: resources[resource].defaultSort,
  });
  const [revision, setRevision] = useState(0);
  const serialized = JSON.stringify({ ...filter, ...page });
  const load = useCallback(
    (signal: AbortSignal) => listRecords(resource, JSON.parse(serialized), signal),
    [resource, serialized],
  );
  const result = useRequest(`${resource}/${serialized}/${revision}`, load, refreshSeconds * 1000);
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">
          {title ?? resources[resource].label}
          {result.data ? ` (${result.data.total})` : ""}
        </h2>
        <Link
          prefetch={false}
          href={`${resourceHref(resource)}?${new URLSearchParams(filter)}`}
          className="text-xs text-blue-700 dark:text-blue-400 hover:underline"
        >
          View full list
        </Link>
      </div>
      <RecordTable
        resource={resource}
        topicId={filter.topic_id}
        page={
          result.data ?? {
            items: [],
            total: 0,
            limit: Number(page.limit),
            offset: Number(page.offset),
          }
        }
        sort={String(page.sort)}
        selectionKey={`${resource}/${serialized}`}
        loading={result.loading}
        error={result.error}
        onRefresh={() => setRevision((value) => value + 1)}
        loadAllRows={(signal) =>
          loadMatchingRows(
            (offset, limit, signal) =>
              listRecords(resource, { ...JSON.parse(serialized), offset, limit }, signal),
            (row) => (resource === "analysis-jobs" ? `${row.kind}/${row.id}` : row.id),
            signal,
          )
        }
        onChange={(changes) =>
          setPage((current) => ({
            ...current,
            ...changes,
            limit: Number(changes.limit ?? current.limit),
            offset: Number(changes.offset ?? current.offset),
          }))
        }
      />
    </section>
  );
}
