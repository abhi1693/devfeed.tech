"use client";

import { useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicProposalsAnalyzeAll } from "@/lib/api/generated/admin";
import { notify, notifyFailure } from "@/lib/notifications";

export function TopicBulkAnalysis({ onQueued }: { onQueued: () => void }) {
  const admin = useAdmin();
  const locked = useRef(false);
  const [busy, setBusy] = useState(false);
  async function run() {
    if (locked.current) return;
    locked.current = true; setBusy(true);
    try {
      const result = await adminTopicProposalsAnalyzeAll({ headers: { "X-CSRF-Token": admin.csrf_token } });
      const counts = [`${result.queued.toLocaleString()} queued`, ...(result.already_active ? [`${result.already_active.toLocaleString()} already queued or running`] : []), ...(result.complete ? [`${result.complete.toLocaleString()} already complete`] : [])];
      notify.success(result.pending ? `AI analysis: ${counts.join(" · ")}` : "No pending proposals to analyze");
      onQueued();
    } catch (error) { notifyFailure(error, "Could not queue bulk AI analysis"); }
    finally { locked.current = false; setBusy(false); }
  }
  return <Tooltip><TooltipTrigger asChild><Button variant="outline" size="sm" loading={busy} loadingText="Queuing analysis…" onClick={() => void run()}>
    <Sparkles aria-hidden />Analyze all pending
  </Button></TooltipTrigger><TooltipContent>Analyze missing information for all pending proposals, across every page and filter. Already queued or running proposals are skipped.</TooltipContent></Tooltip>;
}
