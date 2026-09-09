"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, Eye, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { useAdmin } from "./admin-session";
import { adminRelationshipProposalReview } from "@/lib/api/generated/admin";
import type { RelationshipProposalOut } from "@/lib/api/generated/models";
import { humanize } from "@/lib/resources";
import { notify, notifyFailure } from "@/lib/notifications";

export const relationshipLabel = (row: Pick<RelationshipProposalOut, "topic_name" | "related_topic_name" | "relation">) => `${row.topic_name} → ${row.related_topic_name} (${humanize(row.relation)})`;

export function RelationshipProposalActions({ proposal, onRefresh, reviewLink = true }: { proposal: RelationshipProposalOut; onRefresh?: () => void; reviewLink?: boolean }) {
  const admin = useAdmin(); const [busy, setBusy] = useState(false);
  async function review(decision: "approved" | "rejected") {
    if (busy) return;
    setBusy(true);
    try {
      await adminRelationshipProposalReview(proposal.id, { decision, expected_input_hash: proposal.content_hash }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      notify.success(decision === "approved" ? "Relationship approved" : "Relationship rejected"); onRefresh?.();
    } catch (error) { notifyFailure(error, "Could not review relationship"); }
    finally { setBusy(false); }
  }
  return <div className="flex justify-end gap-1">
    {reviewLink && <Button variant="outline" size="icon-sm" asChild><Link href={`/taxonomy/relationships/proposals/${proposal.id}`} aria-label={`Review ${relationshipLabel(proposal)}`} title="Review"><Eye aria-hidden /></Link></Button>}
    {proposal.status === "pending" && <>
      <Button variant="destructive-ghost" size="icon-sm" aria-label={`Reject ${relationshipLabel(proposal)}`} title="Reject" disabled={busy} onClick={() => void review("rejected")}><X aria-hidden /></Button>
      <Button variant="ghost" size="icon-sm" className="text-emerald-700" aria-label={`Approve ${relationshipLabel(proposal)}`} title={proposal.approval_blocker ?? "Approve"} disabled={busy || !proposal.can_approve} onClick={() => void review("approved")}><Check aria-hidden /></Button>
    </>}
  </div>;
}
