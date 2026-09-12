"use client";

import { DateTime } from "@/components/molecules/date-time";
import Link from "next/link";
import { useMemo, type ReactNode } from "react";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RecordActions } from "@/components/molecules/record-actions";
import { RecordLink } from "@/components/molecules/record-link";
import { relationshipLabel } from "@/components/molecules/relationship-proposal-actions";
import { type Resource, resources, humanize, recordHref } from "@/lib/resources";
import type { RecordData, RecordPage } from "@/lib/resource-api";
import { languageName } from "@/lib/languages";
import { useAdmin } from "@/components/molecules/admin-session";
import { Check, X, Download, Trash2, Sparkles, RotateCcw } from "lucide-react";
import type { BulkAction } from "@/components/molecules/table-bulk-actions";
import { deleteRecord, jobKinds, retryRecordJob } from "@/lib/resource-api";
import { adminArticleReview, adminSourceReview, adminSourceFetch, adminTopicRelationshipsAnalyze, adminRelationshipProposalReview, adminRelationshipProposalDelete } from "@/lib/api/generated/admin";
import type { RelationshipProposalOut } from "@/lib/api/generated/models";

import { relationshipTableColumns, relationshipProposal as proposalFor } from "@/components/molecules/relationship-table-columns";
export function RecordTable({ resource, topicId, toolbar, page, sort, onChange, onRefresh, selectionKey, loading, error, loadAllRows, loadFailedRows }: { resource: Resource; topicId?: string; toolbar?: ReactNode; page: RecordPage; sort: string; onChange: (changes: Record<string, string>) => void; onRefresh?: () => void; selectionKey?: string; loading?: boolean; error?: Error; loadAllRows?: (signal: AbortSignal) => Promise<RecordData[]>; loadFailedRows?: (signal: AbortSignal) => Promise<RecordData[]> }) {
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
    description: "Research relationships for each selected active topic using the internet. Suggestions appear in the relationships table for approval.",
    eligible: row => row.status === "active", run: row => adminTopicRelationshipsAnalyze(row.id, {}, options) });
  if (resource === "topic-relations") {
    for (const decision of ["approved", "rejected"] as const) bulkActions.push({
      id: decision, label: decision === "approved" ? "Approve" : "Reject", icon: decision === "approved" ? <Check aria-hidden /> : <X aria-hidden />,
      description: `${decision === "approved" ? "Approve" : "Reject"} the selected pending relationship suggestions.`,
      eligible: row => row.status === "pending" && !!proposalFor(row) && (decision !== "approved" || !!proposalFor(row)?.can_approve),
      run: row => { const proposal = proposalFor(row)!; return adminRelationshipProposalReview(proposal.id, { decision, expected_input_hash: proposal.content_hash }, options); },
    });
  }
  if (!spec.readonly) bulkActions.push({ id: "delete", label: "Delete", icon: <Trash2 aria-hidden />, destructive: true,
    description: resource === "topics" ? "Permanently delete the selected topics, unpublish their linked articles, and remove article and tag topic links, relationships, relationship proposals and relationship research runs. Articles are kept and will need review before publication. To relink articles to another topic, use the individual topic’s delete form." : resource === "sources" ? "Permanently delete the selected sources and their links, follows, review history, and all source jobs, including queued and running jobs. Articles are kept; they remain visible in the public feed only if another approved source is linked. This cannot be undone." : `Permanently delete the selected ${spec.label.toLowerCase()}. Linked content or active jobs may prevent deletion. ${resource === "articles" ? "Completed jobs, evidence, classifications and review history will also be removed. Feeds may ingest these articles again. " : ""}This cannot be undone.`,
    eligible: row => resource !== "articles" || row.publication_status !== "published",
    run: row => {
      const proposal = resource === "topic-relations" ? proposalFor(row) : null;
      return proposal ? adminRelationshipProposalDelete(proposal.id, { expected_input_hash: proposal.content_hash, expected_status: proposal.status }, options) : deleteRecord(resource, row.id, admin.csrf_token);
    } });
  const columns = useMemo<DataTableColumn<RecordData>[]>(() => resource === "topic-relations" ? relationshipTableColumns(topicId, onRefresh) : [
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
        if (column.resource) return <RecordLink resource={column.resource} id={String(value)} label={row.original.kind && row.original.target_name ? String(row.original.target_name) : undefined} />;
        if (column.key === spec.title || column.key === "id") return <Link prefetch={false} href={recordHref(resource, row.original)} className="block max-w-lg break-words font-medium text-blue-700 dark:text-blue-400 hover:underline">{column.key === "id" ? String(value).slice(0, 8) : String(value)}</Link>;
        if (column.date) return <span className="whitespace-nowrap text-xs"><DateTime value={String(value)} /></span>;
        if (column.key === "language") return languageName(String(value));
        if (typeof value === "boolean" || column.key.endsWith("status")) return <StatusBadge value={value} />;
        return column.key === "email" ? String(value) : humanize(String(value));
      } })),
    ...(!spec.readonly ? [{ id: "actions", header: "Actions", enableSorting: false, cell: ({ row }: { row: { original: RecordData } }) => <RecordActions resource={resource} id={row.original.id} /> }] : []),
  ], [spec, resource, topicId, onRefresh]);
  return <DataTable toolbar={toolbar} columnChoices preferenceKey={`${resource}${topicId ? "-related" : ""}`} label={spec.label} data={page.items} columns={columns} getRowId={row => resource === "analysis-jobs" ? `${row.kind}/${row.id}` : row.id}
    getRowLabel={row => resource === "topic-relations" && row.topic_name && row.related_topic_name ? relationshipLabel(row as unknown as RelationshipProposalOut) : String(row[spec.title] || row.id)} selectionKey={selectionKey} bulkActions={bulkActions} onBulkComplete={onRefresh} loadAllRows={loadAllRows}
    loading={loading} error={error} onRetry={onRefresh}
    bulkSources={jobKinds[resource] && loadFailedRows ? [{ label: "Retry all failed", action: { ...retryAction, description: "Queue retries for unresolved failures matching the current search and filters across all pages. Previous failures with a newer run are excluded. Jobs that can no longer be retried will be listed with a reason." }, loadRows: loadFailedRows }] : []}
    sort={sort} onSortChange={value => onChange({ sort: value || spec.defaultSort, offset: "0" })}
    pagination={{ ...page, onChange }} empty={<span className="text-muted-foreground">{resource === "analysis-jobs" ? "No analysis runs match these filters." : resource === "topic-relations" ? "No relationships or pending suggestions match these filters." : `No ${spec.label.toLowerCase()} match these filters.`}</span>} />;
}
