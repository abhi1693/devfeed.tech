"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Button } from "@devfeed/ui/button";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RequestState } from "@/components/molecules/request-state";
import { DateTime } from "@/components/molecules/date-time";
import { AnalysisStepList } from "@/components/molecules/analysis-step-list";
import { useRequest } from "@/lib/use-request";
import { useArticlePipelineInterval } from "@/lib/use-refresh-interval";
import { humanize } from "@/lib/resources";
import {
  advanceArticlePipeline,
  getArticlePipelineDetail,
  pipelineJobHref,
  pipelineLabels,
} from "@/lib/article-pipeline";
import { useAdmin } from "@/components/molecules/admin-session";
import { notify, notifyFailure } from "@/lib/notifications";
import { ApiError } from "@/lib/api/client";

export function ArticleReadiness({ id }: { id: string }) {
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<Error>();
  const admin = useAdmin();
  const seconds = useArticlePipelineInterval();
  const load = useCallback(
    async (signal: AbortSignal) => ({
      ...(await getArticlePipelineDetail(id, signal)),
      receivedAt: new Date().toISOString(),
    }),
    [id],
  );
  const result = useRequest(`article-readiness/${id}/${revision}`, load, seconds * 1000);
  const terminal = result.data?.stage === "published" || result.data?.stage === "rejected";
  async function advance() {
    if (busy || !result.data?.allowed_actions?.includes("advance")) return;
    setBusy(true);
    setActionError(undefined);
    try {
      await advanceArticlePipeline(id, result.data.editorial_revision, admin.csrf_token);
      notify.success("Next processing step requested");
      setRevision((value) => value + 1);
    } catch (cause) {
      setActionError(
        cause instanceof ApiError ? cause : new Error("Could not resume processing. Try again."),
      );
      notifyFailure(cause, "Could not resume processing");
      setRevision((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ResourcePanel title="Publication readiness">
      <div className="space-y-4">
        <RequestState
          loading={result.loading}
          error={result.error}
          retry={() => setRevision((value) => value + 1)}
        />
        <RequestState error={actionError} />
        {result.data && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm">
                <strong>{pipelineLabels[result.data.stage]}</strong> ·{" "}
                <StatusBadge value={result.data.state} />
              </p>
              {result.data.allowed_actions?.includes("advance") && (
                <Button
                  size="sm"
                  loading={busy}
                  loadingText="Requesting…"
                  onClick={() => void advance()}
                >
                  {result.data.advance_label || "Run next processing step"}
                </Button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Updates every {seconds} seconds while visible. Updated{" "}
              <DateTime value={result.data.receivedAt} />.
            </p>
            <p className="text-sm text-muted-foreground">
              {result.data.stage === "published"
                ? "This article is published. Unpublish it before changing or reprocessing it."
                : result.data.stage === "rejected"
                  ? "This article was rejected. The editorial decision is complete."
                  : result.data.can_publish
                    ? "All publication requirements are met."
                    : "Publication waits until processing and editorial requirements are complete."}
            </p>
            {!!result.data.checks.length && (
              <ul
                className="divide-y rounded-md border px-3"
                aria-label="Publication readiness checks"
              >
                {result.data.checks.map((check) => (
                  <li key={check.code} className="space-y-1 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span className="font-medium">{check.label}</span>
                      <StatusBadge
                        value={check.status}
                        label={
                          check.status === "passed"
                            ? "Complete"
                            : check.status === "blocked"
                              ? "Needs attention"
                              : "Waiting"
                        }
                        tone={
                          check.status === "passed"
                            ? "success"
                            : check.status === "blocked"
                              ? "danger"
                              : "warning"
                        }
                      />
                    </div>
                    {check.detail && (
                      <p className="break-words text-xs text-muted-foreground">{check.detail}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {(!terminal || Object.values(result.data.jobs).some(Boolean)) && (
              <ol
                className="space-y-2"
                aria-label={terminal ? "Recorded processing runs" : "Current processing runs"}
              >
                {(["enrichment", "analysis", "images"] as const).map((key) => {
                  const job = result.data!.jobs[key];
                  if (terminal && !job) return null;
                  return (
                    <li key={key} className="rounded-md border p-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <strong>{pipelineLabels[key]}</strong>
                        {job ? (
                          <StatusBadge value={job.status} />
                        ) : (
                          <span className="text-xs text-muted-foreground">Not started</span>
                        )}
                      </div>
                      {job && (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          {job.outcome && <p>Outcome: {humanize(job.outcome)}</p>}
                          {job.status === "queued" && (
                            <p>
                              Available <DateTime value={job.available_at} />
                            </p>
                          )}
                          {job.finished_at && (
                            <p>
                              Finished <DateTime value={job.finished_at} />
                            </p>
                          )}
                          <Link
                            prefetch={false}
                            href={pipelineJobHref(key, job.id)}
                            className="inline-block pt-1 text-primary hover:underline"
                          >
                            {job.retryable ? "Open run to retry" : "View run and logs"}
                          </Link>
                        </div>
                      )}
                      {key === "analysis" && (
                        <AnalysisStepList
                          steps={result.data!.analysis_steps ?? []}
                          jobStatus={job?.status}
                        />
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </>
        )}
      </div>
    </ResourcePanel>
  );
}
