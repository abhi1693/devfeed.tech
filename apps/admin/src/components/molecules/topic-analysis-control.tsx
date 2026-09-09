"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminTopicProposalAnalyze, adminTopicProposalGet } from "@/lib/api/generated/admin";
import type { TopicAnalysisOut, TopicProposalOut } from "@/lib/api/generated/models";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { usePolling } from "@/lib/use-polling";
import { RefreshInterval } from "./refresh-interval";
import { notify, notifyFailure } from "@/lib/notifications";

export function analysisActive(job?: TopicAnalysisOut | null) {
  return job?.status === "queued" || job?.status === "running";
}

export function TopicAnalysisControl({ proposal, compact = false, disabled = false, onUpdated, onBusyChange }: {
  proposal: TopicProposalOut; compact?: boolean; disabled?: boolean;
  onUpdated: (proposal: TopicProposalOut) => void; onBusyChange?: (busy: boolean) => void;
}) {
  const admin = useAdmin();
  const [job, setJob] = useState(proposal.analysis);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();
  const [refreshSeconds, setRefreshSeconds] = useRefreshInterval();
  const retryRequest = useRef<AbortController | null>(null);
  const locked = useRef(false);
  const active = analysisActive(job);
  const missing = ["description", "aliases", "keywords", "website_url", "logo_url", "facts"].some(field => {
    const value = proposal.proposed[field as keyof typeof proposal.proposed];
    return !value || (Array.isArray(value) && !value.length);
  });

  useEffect(() => { onBusyChange?.(starting || active); }, [starting, active, onBusyChange]);
  const poll = useCallback(async (signal: AbortSignal) => {
    try {
      const updated = await adminTopicProposalGet(proposal.id, { signal });
      if (signal.aborted) return;
      setJob(updated.analysis); setError(undefined);
      if (!analysisActive(updated.analysis)) onUpdated(updated);
    } catch (error) {
      if (!signal.aborted) setError(error instanceof Error ? error.message : "Could not check analysis status");
    }
  }, [proposal.id, onUpdated]);
  usePolling(signal => poll(signal), active && !error ? refreshSeconds * 1000 : 0, proposal.id);
  useEffect(() => () => retryRequest.current?.abort(), []);

  async function run() {
    if (locked.current || active || disabled || !missing) return;
    locked.current = true; setStarting(true); setError(undefined);
    try {
      const queued = await adminTopicProposalAnalyze(proposal.id, { headers: { "X-CSRF-Token": admin.csrf_token } });
      setJob({ id: queued.id, status: queued.status as TopicAnalysisOut["status"], attempts: queued.attempts,
        created_at: queued.created_at, finished_at: queued.finished_at, error: queued.error, model: null, outcome: null });
      notify.success(`AI analysis queued for ${proposal.proposed.name}`);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not start analysis");
      notifyFailure(error, "Could not start AI analysis");
    } finally { locked.current = false; setStarting(false); }
  }
  const status = starting ? "Queuing…" : job?.status === "queued" ? "Queued" : job?.status === "running" ? "Researching…" : job?.status === "failed" ? "Failed" : job?.outcome === "enriched" ? "Ready for review" : job?.outcome === "superseded" ? "Proposal changed" : job?.outcome === "insufficient_evidence" ? "No supported additions" : "";
  const retryStatus = !!error && active;
  function retryPolling() {
    setError(undefined); retryRequest.current?.abort();
    retryRequest.current = new AbortController();
    void poll(retryRequest.current.signal);
  }
  const tooltip = error ? `${error} ${active ? "Click to retry the status check." : "Click to try again."}` : status || (missing ? "Enrich missing information with AI" : "No missing information");
  const button = <Button variant="outline" size={compact ? "icon-sm" : "sm"} className={error ? "text-destructive" : undefined} aria-label={`${retryStatus ? "Retry AI status" : "Run AI analysis"} for ${proposal.proposed.name}`}
    disabled={disabled || (active && !retryStatus) || (!active && !missing)} loading={starting || (active && !error)} loadingText={compact ? undefined : status} onClick={() => retryStatus ? retryPolling() : void run()}>
    {error ? <CircleAlert aria-hidden /> : <Sparkles aria-hidden />}{!compact && (retryStatus ? "Retry status check" : job ? "Run again" : "Run AI analysis")}
  </Button>;
  return <div className={compact ? "flex shrink-0 items-center" : "space-y-3 rounded-lg border bg-card p-5"}>
    {!compact && <><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">AI analysis</h2><RefreshInterval label="AI status refresh interval" value={refreshSeconds} onChange={setRefreshSeconds} /></div><p className="text-sm text-muted-foreground">Fill missing information using web research. Check the sources before approving.</p></>}
    {proposal.status === "pending" && (compact ? <Tooltip><TooltipTrigger asChild>{button}</TooltipTrigger><TooltipContent>{tooltip}</TooltipContent></Tooltip> : button)}
    {!compact && status && <p role="status" className="text-sm">{status}{job?.model && ` · ${job.model}`}</p>}
    {!compact && job?.error && <p className="text-sm text-destructive">{job.error}</p>}
    {!compact && job?.reasons?.map((reason, index) => <p key={index} className="text-sm text-muted-foreground">{reason}</p>)}
    {!compact && disabled && !active && <p className="text-xs text-muted-foreground">Finish or discard your edits before running AI analysis.</p>}
    {!compact && error && <div role="alert" className="max-w-64 whitespace-normal text-xs text-destructive">{error}</div>}
  </div>;
}
