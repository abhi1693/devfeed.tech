"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { Check, Eye, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";
import { TopicAnalysisControl, analysisActive } from "@/components/molecules/topic-analysis-control";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicProposalReview } from "@/lib/api/generated/admin";
import type { TopicProposalOut, TopicReviewDecision } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

export function TopicProposalActions({ proposal, onReviewed }: { proposal: TopicProposalOut; onReviewed: () => void }) {
  const admin = useAdmin();
  const saving = useRef(false);
  const [decision, setDecision] = useState<TopicReviewDecision>();
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState<{ decision: TopicReviewDecision; message: string }>();
  const [analyzing, setAnalyzing] = useState(analysisActive(proposal.analysis));
  const updated = useCallback(() => onReviewed(), [onReviewed]);
  const pending = proposal.status === "pending" && !reviewed;

  async function review(next: TopicReviewDecision) {
    if (saving.current || !pending || analyzing) return;
    saving.current = true; setDecision(next); setError(undefined);
    try {
      await adminTopicProposalReview(proposal.id, {
        decision: next,
        ...(proposal.content_hash ? { expected_input_hash: proposal.content_hash } : {}),
        ...(next === "approved" ? { topic: proposal.proposed } : {}),
      }, { headers: { "X-CSRF-Token": admin.csrf_token } });
      setReviewed(true);
      notify.success(next === "approved" ? `${proposal.proposed.name} approved` : `${proposal.proposed.name} rejected`);
      onReviewed();
    } catch (error) {
      setError({ decision: next, message: error instanceof Error ? error.message : "Review failed. Try again." });
      notifyFailure(error, "Could not review proposal");
    } finally { saving.current = false; setDecision(undefined); }
  }

  return <div role="group" aria-label={`Actions for ${proposal.proposed.name}`} className="flex h-8 items-center justify-end gap-1">
      <Tooltip><TooltipTrigger asChild><Button variant="outline" size="icon-sm" asChild disabled={!!decision}>
        <Link prefetch={false} href={`/taxonomy/topics/proposals/${proposal.id}`} aria-label={`${pending ? "Review" : "View"} ${proposal.proposed.name}`}><Eye aria-hidden /></Link>
      </Button></TooltipTrigger><TooltipContent>{pending ? "Review and edit" : "View review"}</TooltipContent></Tooltip>
      {pending && <>
        <TopicAnalysisControl compact proposal={proposal} disabled={!!decision} onUpdated={updated} onBusyChange={setAnalyzing} />
        <Tooltip><TooltipTrigger asChild><Button variant="destructive-ghost" size="icon-sm" aria-label={`Reject ${proposal.proposed.name}`}
          disabled={!!decision || analyzing} loading={decision === "rejected"} onClick={() => void review("rejected")}><X aria-hidden /></Button></TooltipTrigger>
          <TooltipContent>{error?.decision === "rejected" ? `${error.message} Click to try again.` : "Reject proposal"}</TooltipContent></Tooltip>
        <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon-sm" className="text-emerald-700 hover:bg-emerald-50 hover:text-emerald-800" aria-label={`Approve ${proposal.proposed.name}`}
          disabled={!!decision || analyzing} loading={decision === "approved"} onClick={() => void review("approved")}><Check aria-hidden /></Button></TooltipTrigger>
          <TooltipContent>{error?.decision === "approved" ? `${error.message} Click to try again.` : "Approve proposed changes"}</TooltipContent></Tooltip>
      </>}
  </div>;
}
