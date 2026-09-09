"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Check, Eye, Sparkles, Trash2, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import type { BulkAction } from "@/components/molecules/table-bulk-actions";
import { FormField } from "@/components/molecules/form-field";
import { adminRouteTitle } from "@/lib/page-titles";
import { PageHeading } from "@/components/molecules/page-heading";
import { RecordLink } from "@/components/molecules/record-link";
import { RequestState } from "@/components/molecules/request-state";
import { StatusBadge } from "@/components/molecules/status-badge";
import { ValidationErrors } from "@/components/molecules/validation-errors";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminRelationshipProposalsList, adminRelationshipProposalGet, adminRelationshipProposalReview, adminRelationshipProposalDelete } from "@/lib/api/generated/admin";
import type { RelationshipProposalOut } from "@/lib/api/generated/models";
import { actorLabel } from "@/lib/actor-label";
import { humanize } from "@/lib/resources";
import { recordHref } from "@/lib/routes";
import { loadMatchingRows } from "@/lib/table-selection";
import { RefreshInterval } from "@/components/molecules/refresh-interval";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";
import { notify, notifyFailure } from "@/lib/notifications";
import { relationshipTrail } from "./relationship-discovery";

const base = "/taxonomy/relationships/proposals";
type Proposal = RelationshipProposalOut;
const label = (row: Proposal) => `${row.topic_name} → ${row.related_topic_name} (${humanize(row.relation)})`;

function RowActions({ proposal, onRefresh }: { proposal: Proposal; onRefresh: () => void }) {
  const admin = useAdmin(); const [busy, setBusy] = useState(false);
  async function review(decision: "approved" | "rejected") {
    if (busy) return;
    setBusy(true);
    try {
      await adminRelationshipProposalReview(proposal.id, { decision, expected_input_hash: proposal.content_hash }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success(decision === "approved" ? "Relationship approved" : "Relationship rejected"); onRefresh();
    } catch (error) { notifyFailure(error, "Could not review relationship"); }
    finally { setBusy(false); }
  }
  return <div className="flex justify-end gap-1">
    <Button variant="outline" size="icon-sm" asChild><Link href={`${base}/${proposal.id}`} aria-label={`Review ${label(proposal)}`} title="Review"><Eye aria-hidden /></Link></Button>
    {proposal.status === "pending" && <>
      <Button variant="destructive-ghost" size="icon-sm" aria-label={`Reject ${label(proposal)}`} title="Reject" disabled={busy} onClick={() => void review("rejected")}><X aria-hidden /></Button>
      <Button variant="ghost" size="icon-sm" className="text-emerald-700" aria-label={`Approve ${label(proposal)}`} title={proposal.approval_blocker ?? "Approve"} disabled={busy || !proposal.can_approve} onClick={() => void review("approved")}><Check aria-hidden /></Button>
    </>}
  </div>;
}

export function RelationshipProposals() {
  const [refreshSeconds, setRefreshSeconds] = useRefreshInterval();
  const admin = useAdmin(); const router = useRouter(); const search = useSearchParams();
  const [revision, setRevision] = useState(0);
  const status = (["pending", "approved", "rejected"].includes(search.get("status") ?? "") ? search.get("status") : "pending") as Proposal["status"];
  const q = (search.get("q") ?? "").trim().slice(0, 200);
  const topicId = search.get("topic_id") || undefined; const jobId = search.get("job_id") || undefined;
  const offset = Math.max(0, Number(search.get("offset")) || 0);
  const limit = [10, 25, 50, 100].includes(Number(search.get("limit"))) ? Number(search.get("limit")) : 25;
  const sort = ["created_at", "-created_at", "relation", "-relation", "status", "-status"].includes(search.get("sort") ?? "") ? search.get("sort")! : "-created_at";
  const load = useCallback((signal: AbortSignal) => adminRelationshipProposalsList({ status, q, topic_id: topicId, job_id: jobId, offset, limit, sort }, { signal }), [status, q, topicId, jobId, offset, limit, sort]);
  const result = useRequest(JSON.stringify([status, q, topicId, jobId, offset, limit, sort, revision]), load, refreshSeconds * 1000);
  const refresh = () => setRevision(value => value + 1);
  function href(values: Record<string, string>) { const params = new URLSearchParams(search); for (const [key, value] of Object.entries(values)) { if (value) params.set(key, value); else params.delete(key); } return `${base}?${params}`; }
  function change(values: Record<string, string>, replace = false) { router[replace ? "replace" : "push"](href(values), { scroll: false }); }
  const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
  const actions: BulkAction<Proposal>[] = [
    { id: "approve", label: "Approve", icon: <Check aria-hidden />, description: "Create the selected relationships. Both topics must still be active and unchanged since research.", eligible: row => row.status === "pending" && row.can_approve, run: row => adminRelationshipProposalReview(row.id, { decision: "approved", expected_input_hash: row.content_hash }, options) },
    { id: "reject", label: "Reject", icon: <X aria-hidden />, description: "Reject the selected pending relationship suggestions.", eligible: row => row.status === "pending", run: row => adminRelationshipProposalReview(row.id, { decision: "rejected", expected_input_hash: row.content_hash }, options) },
    { id: "delete", label: "Delete", icon: <Trash2 aria-hidden />, destructive: true, description: "Permanently delete these proposals. Approved relationships remain in the catalog. Deleted suggestions may be proposed again in future research.", run: row => adminRelationshipProposalDelete(row.id, { expected_input_hash: row.content_hash, expected_status: row.status }, options) },
  ];
  const columns: DataTableColumn<Proposal>[] = [
    { id: "topic", header: "From topic", cell: ({ row }) => <RecordLink resource="topics" id={row.original.topic_id} label={row.original.topic_name} /> },
    { id: "relation", accessorKey: "relation", header: "Relationship", enableSorting: true, cell: ({ row }) => humanize(row.original.relation) },
    { id: "related_topic", header: "To topic", cell: ({ row }) => <RecordLink resource="topics" id={row.original.related_topic_id} label={row.original.related_topic_name} /> },
    { id: "evidence", header: "Evidence", cell: ({ row }) => <a className="block max-w-72 truncate text-primary hover:underline" href={row.original.evidence_url} target="_blank" rel="noopener noreferrer" title={row.original.evidence_title}>{row.original.evidence_title}</a> },
    { id: "status", accessorKey: "status", header: "Status", enableSorting: true, cell: ({ row }) => <div><StatusBadge value={row.original.status} />{row.original.approval_blocker && <span className="mt-1 block max-w-48 text-xs text-muted-foreground" title={row.original.approval_blocker}>Needs new research</span>}</div> },
    { id: "created_at", accessorKey: "created_at", header: "Submitted", enableSorting: true, cell: ({ row }) => <span className="whitespace-nowrap text-xs">{new Date(row.original.created_at).toLocaleDateString()}</span> },
    { id: "actions", header: "Actions", enableHiding: false, meta: { className: "w-32", headerClassName: "w-32 text-right" }, cell: ({ row }) => <RowActions proposal={row.original} onRefresh={refresh} /> },
  ];
  return <section className="min-w-0 space-y-6">
    <PageHeading title="Relationship proposals" trail={relationshipTrail} description="Review AI suggestions before connecting active topics.">
      <Button variant="outline" size="sm" asChild><Link href="/jobs/analysis/topics">Research runs</Link></Button>
      <Button size="sm" asChild><Link href="/taxonomy/relationships/discover"><Sparkles aria-hidden />Discover relationships</Link></Button>
    </PageHeading>
    <nav aria-label="Relationship proposal status" className="flex gap-6 border-b">{(["pending", "approved", "rejected"] as const).map(value => <Link key={value} href={href({ status: value, offset: "0" })} aria-current={status === value ? "page" : undefined} className={`border-b-2 px-1 pb-3 text-sm ${status === value ? "border-primary font-medium" : "border-transparent text-muted-foreground"}`}>{humanize(value)}{status === value && result.data && <span className="ml-2 rounded-md bg-muted px-1.5 py-0.5 text-xs">{result.data.total}</span>}</Link>)}</nav>
    <DataTable label="Relationship proposals" data={result.data?.items ?? []} columns={columns} getRowId={row => row.id} getRowLabel={label}
      sort={sort} onSortChange={sort => change({ sort, offset: "0" })} columnChoices loading={result.loading} error={result.error} onRetry={refresh}
      empty="No relationship proposals match these filters." pagination={{ offset, limit, total: result.data?.total ?? 0, onChange: change }}
      bulkActions={actions} onBulkComplete={refresh} selectionKey={JSON.stringify([status, q, topicId, jobId])}
      loadAllRows={signal => loadMatchingRows((offset, limit, signal) => adminRelationshipProposalsList({ status, q, topic_id: topicId, job_id: jobId, offset, limit, sort }, { signal }), row => row.id, signal)}
      toolbar={<div className="flex flex-wrap items-center gap-2"><SearchField className="max-w-lg flex-1" label="Search relationship proposals" placeholder="Search topics, relationships…" value={q} scopeKey={searchScope(search.toString())} onSearch={q => change({ q, offset: "0" }, true)} /><RefreshInterval value={refreshSeconds} onChange={setRefreshSeconds} loading={result.loading || result.refreshing} />{(q || topicId || jobId) && <Button variant="ghost" onClick={() => change({ q: "", topic_id: "", job_id: "", offset: "0" })}>Clear filters</Button>}</div>} />
  </section>;
}

export function RelationshipProposalReview({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => adminRelationshipProposalGet(id, { signal }), [id]);
  const result = useRequest(id, load);
  return <><RequestState loading={result.loading} error={result.error} />{result.data && <Review key={id} initial={result.data} />}</>;
}
function Review({ initial }: { initial: Proposal }) {
  const admin = useAdmin(); const [proposal, setProposal] = useState(initial);
  const [note, setNote] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  const reviewed = proposal.status !== "pending";
  async function review(decision: "approved" | "rejected") {
    if (busy || reviewed) return;
    setBusy(true); setError(undefined);
    try {
      setProposal(await adminRelationshipProposalReview(proposal.id, { decision, expected_input_hash: proposal.content_hash, note: note.trim() || null }, { headers: { "X-CSRF-Token": admin.csrf_token } }));
      notify.success(decision === "approved" ? "Relationship approved" : "Relationship rejected");
    } catch (error) { setError(error instanceof Error ? error : new Error("Review failed")); notifyFailure(error, "Could not review relationship"); }
    finally { setBusy(false); }
  }
  return <section className="min-w-0 space-y-6">
    <PageHeading browserTitle={adminRouteTitle({ view: "relationship-proposal", id: proposal.id }, `${proposal.topic_name} → ${proposal.related_topic_name}`)} title="Review relationship" trail={[...relationshipTrail, { label: "Proposals", href: base }]} description={humanize(proposal.status)}>
      <Button variant="outline" size="sm" asChild><Link href={recordHref("analysis-jobs", { id: proposal.job_id, kind: "topic-analysis" })}>Research run</Link></Button>
      {proposal.status === "approved" && <Button size="sm" asChild><Link href={`/taxonomy/relationships?topic_id=${encodeURIComponent(proposal.topic_id)}`}>View relationships</Link></Button>}
    </PageHeading>
    <form className="max-w-3xl space-y-5 rounded-lg border bg-card p-5" onSubmit={event => { event.preventDefault(); if (proposal.can_approve) void review("approved"); }}>
      <div className="flex flex-wrap items-center gap-3 text-sm"><RecordLink resource="topics" id={proposal.topic_id} label={proposal.topic_name} /><span className="rounded-md bg-muted px-2 py-1">{humanize(proposal.relation)} →</span><RecordLink resource="topics" id={proposal.related_topic_id} label={proposal.related_topic_name} /></div>
      <p className="whitespace-pre-wrap break-words text-sm">{proposal.explanation}</p>
      <div className="space-y-2 border-l-2 pl-4 text-sm"><a href={proposal.evidence_url} target="_blank" rel="noopener noreferrer" className="break-words font-medium text-primary hover:underline">{proposal.evidence_title}</a><blockquote className="whitespace-pre-wrap break-words text-muted-foreground">{proposal.evidence_quote}</blockquote><p className="text-xs text-muted-foreground">AI-supplied evidence. Verify the source before approving.</p></div>
      <p className="text-xs text-muted-foreground">Requested by {actorLabel(proposal.created_by, admin)} · {new Date(proposal.created_at).toLocaleString()}</p>
      {proposal.reviewed_at && proposal.reviewed_by && <p className="text-xs text-muted-foreground">Reviewed by {actorLabel(proposal.reviewed_by, admin)} · {new Date(proposal.reviewed_at).toLocaleString()}</p>}
      <ValidationErrors error={error} />
      {proposal.approval_blocker && <p role="status" className="rounded-md bg-muted p-3 text-sm">{proposal.approval_blocker}</p>}
      <FormField field={{ key: "note", label: "Review note", type: "textarea", max: 1000 }} value={reviewed ? proposal.review_note ?? "" : note} disabled={busy || reviewed} onChange={value => setNote(String(value))} />
      {!reviewed && <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" asChild><Link href={base}>Cancel</Link></Button><Button variant="destructive-ghost" disabled={busy} onClick={() => void review("rejected")}>Reject</Button><Button type="submit" disabled={!proposal.can_approve} loading={busy} loadingText="Saving review…">Approve relationship</Button></div>}
    </form>
  </section>;
}
