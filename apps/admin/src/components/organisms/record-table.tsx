"use client";
import Link from "next/link";
import { useMemo } from "react";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RecordActions } from "@/components/molecules/record-actions";
import { RecordLink } from "@/components/molecules/record-link";
import { type Resource, resources, humanize, recordHref } from "@/lib/resources";
import type { RecordData, RecordPage } from "@/lib/resource-api";
import { languageName } from "@/lib/languages";
export function RecordTable({ resource, page, sort, onChange }: { resource: Resource; page: RecordPage; sort: string; onChange: (changes: Record<string, string>) => void }) {
  const spec = resources[resource];
  const columns = useMemo<DataTableColumn<RecordData>[]>(() => [
    ...spec.columns.map(column => ({ id: column.key, accessorKey: column.key, header: column.label, enableSorting: !!column.sort,
      cell: ({ row }: { row: { original: RecordData } }) => {
        const value = row.original[column.key];
        if (resource === "analysis-jobs" && column.key === "target_name") {
          const record = row.original;
          if (record.proposal_id) return <Link prefetch={false} href={`/taxonomy/topics/proposals/${encodeURIComponent(String(record.proposal_id))}`} className="block max-w-80 truncate font-medium hover:underline" title={String(value || "Topic proposal")}>{String(value || "Topic proposal")}</Link>;
          if (record.article_id) return value ? <Link prefetch={false} href={`/content/articles/${encodeURIComponent(String(record.article_id))}`} className="block max-w-80 truncate font-medium hover:underline" title={String(value)}>{String(value)}</Link> : <RecordLink resource="articles" id={String(record.article_id)} />;
        }
        if (resource === "analysis-jobs" && column.key === "kind") return row.original.kind === "topic-analysis" ? "Topic" : "Article";
        if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
        if (column.resource) return <RecordLink resource={column.resource} id={String(value)} />;
        if (column.key === spec.title || column.key === "id") return <Link prefetch={false} href={recordHref(resource, row.original)} className="block max-w-lg break-words font-medium text-blue-700 hover:underline">{column.key === "id" ? String(value).slice(0, 8) : String(value)}</Link>;
        if (column.date) return <span className="whitespace-nowrap text-xs">{new Date(String(value)).toLocaleString()}</span>;
        if (column.key === "language") return languageName(String(value));
        if (typeof value === "boolean" || column.key.endsWith("status")) return <StatusBadge value={value} />;
        return humanize(String(value));
      } })),
    ...(!spec.readonly ? [{ id: "actions", header: "Actions", enableSorting: false, cell: ({ row }: { row: { original: RecordData } }) => <RecordActions resource={resource} id={row.original.id} /> }] : []),
  ], [spec, resource]);
  return <DataTable label={spec.label} data={page.items} columns={columns} getRowId={row => resource === "analysis-jobs" ? `${row.kind}/${row.id}` : row.id}
    sort={sort} onSortChange={value => onChange({ sort: value || spec.defaultSort, offset: "0" })}
    pagination={{ ...page, onChange }} empty={<span className="text-muted-foreground">{resource === "analysis-jobs" ? "No analysis runs match these filters." : `No ${spec.label.toLowerCase()} match these filters.`}</span>} />;
}
