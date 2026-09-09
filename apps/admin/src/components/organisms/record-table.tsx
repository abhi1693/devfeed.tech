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
import { useAdmin } from "@/components/molecules/admin-session";
import { Check, X, Download, Trash2, Sparkles, RotateCcw } from "lucide-react";
import type { BulkAction } from "@/components/molecules/table-bulk-actions";
import { deleteRecord, jobKinds, retryRecordJob } from "@/lib/resource-api";
import { adminArticleReview, adminSourceReview, adminSourceFetch, adminTopicRelationshipsAnalyze } from "@/lib/api/generated/admin";
export function RecordTable({ resource, page, sort, onChange, onRefresh, selectionKey, loading, error, loadAllRows, loadFailedRows }: { resource: Resource; page: RecordPage; sort: string; onChange: (changes: Record<string, string>) => void; onRefresh?: () => void; selectionKey?: string; loading?: boolean; error?: Error; loadAllRows?: (signal: AbortSignal) => Promise<RecordData[]>; loadFailedRows?: (signal: AbortSignal) => Promise<RecordData[]> }) {
  const admin = useAdmin();
  const spec = resources[resource];
  const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
  const bulkActions: BulkAction<RecordData>[] = [];
  const retryAction: BulkAction<RecordData> = { id: "retry", label: "Retry", icon: <RotateCcw aria-hidden />,
    description: "Queue retries for the selected unresolved failures. Jobs that can no longer be retried will be listed with a reason.",
    eligible: row => row.status === "failed" && row.retryable === true, run: row => retryRecordJob(resource, row, admin.csrf_token) };
  if (jobKinds[resource]) bulkActions.push(retryAction);
  if (resource === "articles" || resource === "sources") {
    for (const decision of ["approve", "reject"] as const) bulkActions.push({
      id: decision, label: decision === "approve" ? "Approve" : "Reject", icon: decision === "approve" ? <Check aria-hidden /> : <X aria-hidden />,
      description: `${decision === "approve" ? "Approve" : "Reject"} the selected pending ${spec.label.toLowerCase()}.`,
      eligible: row => (resource === "sources" ? row.approval_status : row.review_status) === "pending" && (resource !== "articles" || typeof row.editorial_revision === "number"),
      run: row => resource === "articles"
        ? adminArticleReview(row.id, { action: decision, expected_revision: Number(row.editorial_revision) }, options)
        : adminSourceReview(row.id, { decision: decision === "approve" ? "approved" : "rejected" }, options),
    });
  }
  if (resource === "sources") bulkActions.push({ id: "fetch", label: "Fetch", icon: <Download aria-hidden />, description: "Request a feed fetch for each selected approved source.",
    eligible: row => row.approval_status === "approved", run: row => adminSourceFetch(row.id, options) });
  if (resource === "topics") bulkActions.push({ id: "relationships", label: "Discover relationships", icon: <Sparkles aria-hidden />,
    description: "Research relationships for each selected active topic using the internet. Suggestions require approval in Topic relationships → Review proposals.",
    eligible: row => row.status === "active", run: row => adminTopicRelationshipsAnalyze(row.id, {}, options) });
  if (!spec.readonly) bulkActions.push({ id: "delete", label: "Delete", icon: <Trash2 aria-hidden />, destructive: true,
    description: `Permanently delete the selected ${spec.label.toLowerCase()}. Linked content or active jobs may prevent deletion. ${resource === "articles" ? "Completed jobs, evidence, classifications and review history will also be removed. Feeds may ingest these articles again. " : resource === "sources" ? "Completed jobs and review history will also be removed. " : ""}This cannot be undone.`,
    eligible: row => resource !== "articles" || row.publication_status !== "published",
    run: row => deleteRecord(resource, row.id, admin.csrf_token) });
  const columns = useMemo<DataTableColumn<RecordData>[]>(() => [
    ...spec.columns.map(column => ({ id: column.key, accessorKey: column.key, header: column.label, enableSorting: !!column.sort,
      cell: ({ row }: { row: { original: RecordData } }) => {
        const value = row.original[column.key];
        if (resource === "analysis-jobs" && column.key === "target_name") {
          const record = row.original;
          if (record.proposal_id) return <Link prefetch={false} href={`/taxonomy/topics/proposals/${encodeURIComponent(String(record.proposal_id))}`} className="block max-w-80 truncate font-medium hover:underline" title={String(value || "Topic proposal")}>{String(value || "Topic proposal")}</Link>;
          if (record.topic_id) return <RecordLink resource="topics" id={String(record.topic_id)} label={String(value || "Topic")} />;
          if (record.article_id) return value ? <Link prefetch={false} href={`/content/articles/${encodeURIComponent(String(record.article_id))}`} className="block max-w-80 truncate font-medium hover:underline" title={String(value)}>{String(value)}</Link> : <RecordLink resource="articles" id={String(record.article_id)} />;
        }
        if (resource === "analysis-jobs" && column.key === "kind") return row.original.topic_id ? "Relationships" : row.original.kind === "topic-analysis" ? "Topic" : "Article";
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
    getRowLabel={row => String(row[spec.title] || row.id)} selectionKey={selectionKey} bulkActions={bulkActions} onBulkComplete={onRefresh} loadAllRows={loadAllRows}
    bulkSources={jobKinds[resource] && loadFailedRows ? [{ label: "Retry all failed", action: { ...retryAction, description: "Queue retries for unresolved failures matching the current search and filters across all pages. Previous failures with a newer run are excluded. Jobs that can no longer be retried will be listed with a reason." }, loadRows: loadFailedRows }] : []}
    loading={loading} error={error} onRetry={onRefresh}
    sort={sort} onSortChange={value => onChange({ sort: value || spec.defaultSort, offset: "0" })}
    pagination={{ ...page, onChange }} empty={<span className="text-muted-foreground">{resource === "analysis-jobs" ? "No analysis runs match these filters." : `No ${spec.label.toLowerCase()} match these filters.`}</span>} />;
}
