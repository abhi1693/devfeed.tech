import type { RelationshipProposalOut } from "@/lib/api/generated/models";
import type { RecordData } from "@/lib/resource-api";
import { humanize } from "@/lib/resources";
import type { DataTableColumn } from "./data-table";
import { RecordActions } from "./record-actions";
import { RecordLink } from "./record-link";
import { RelationshipProposalActions } from "./relationship-proposal-actions";
import { StatusBadge } from "./status-badge";

export const relationshipProposal = (row: RecordData) => row.proposal as RelationshipProposalOut | null | undefined;
const incomingRelations: Record<string, string> = { uses_language: "Used by", depends_on: "Required by", implements: "Implemented by", part_of: "Contains", related_to: "Related to" };

export function relationshipTableColumns(topicId?: string, onRefresh?: () => void): DataTableColumn<RecordData>[] {
  const topicColumn = (key: "topic_id" | "related_topic_id", label: string): DataTableColumn<RecordData> => ({
    id: key, header: label,
    cell: ({ row: { original: row } }) => {
      const from = topicId ? row.related_topic_id === topicId : key === "topic_id";
      return <div className="max-w-56 whitespace-normal [overflow-wrap:anywhere]"><RecordLink resource="topics" id={String(row[from ? "topic_id" : "related_topic_id"])} label={row[from ? "topic_name" : "related_topic_name"] as string | undefined} /></div>;
    },
  });
  return [
    ...(topicId ? [topicColumn("related_topic_id", "Related topic")] : [topicColumn("topic_id", "From topic"), topicColumn("related_topic_id", "To topic")]),
    { id: "relation", accessorKey: "relation", header: "Relationship", enableSorting: true, cell: ({ row: { original: row } }) => {
      const proposal = relationshipProposal(row);
      const incoming = topicId && row.related_topic_id === topicId;
      return <div className="max-w-80 whitespace-normal [overflow-wrap:anywhere]"><span>{incoming ? incomingRelations[String(row.relation)] : humanize(String(row.relation))}</span>{proposal && <p className="mt-1 text-xs text-muted-foreground">{proposal.explanation}</p>}</div>;
    } },
    { id: "status", accessorKey: "status", header: "Status", enableSorting: true, cell: ({ row: { original: row } }) => <div className="max-w-48 space-y-1 whitespace-normal [overflow-wrap:anywhere]"><StatusBadge value={row.status || "approved"} />{relationshipProposal(row)?.approval_blocker && <p className="text-xs text-muted-foreground">{relationshipProposal(row)?.approval_blocker}</p>}</div> },
    { id: "evidence_url", header: "Evidence", cell: ({ row: { original: row } }) => {
      const proposal = relationshipProposal(row);
      return row.evidence_url ? <div className="max-w-80 space-y-1 whitespace-normal [overflow-wrap:anywhere]"><a href={String(row.evidence_url)} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{proposal?.evidence_title || "View source"}</a>{proposal && <blockquote className="text-xs text-muted-foreground">{proposal.evidence_quote}</blockquote>}</div> : <span className="text-muted-foreground">—</span>;
    } },
    { id: "actions", header: "Actions", enableSorting: false, enableHiding: false, cell: ({ row: { original: row } }) => relationshipProposal(row)
      ? <RelationshipProposalActions proposal={relationshipProposal(row)!} onRefresh={onRefresh} reviewLink={false} />
      : <RecordActions resource="topic-relations" id={row.id} /> },
  ];
}
