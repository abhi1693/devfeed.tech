"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@devfeed/ui/button";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@devfeed/ui/dropdown-menu";
import { useAdmin } from "@/components/molecules/admin-session";
import { RequestState } from "@/components/molecules/request-state";
import { StatusBadge } from "@/components/molecules/status-badge";
import { DateTime } from "@/components/molecules/date-time";
import { useRequest } from "@/lib/use-request";
import { useBoardRefreshInterval } from "@/lib/use-refresh-interval";
import { humanize } from "@/lib/resources";
import { notify, notifyFailure } from "@/lib/notifications";
import {
  advanceSourcePipeline,
  getSourcePipelineDetail,
  retrySourcePipeline,
  sourcePipelineJobHref,
  sourcePipelineLabels,
  sourceRetryLabel,
  type SourcePipelineKind,
  type SourcePipelineCard,
  type SourcePipelineDetail,
} from "@/lib/source-pipeline";
import { SourceRelevanceAssessment } from "./source-relevance-assessment";

type RetryTarget = SourcePipelineCard["retry_targets"][number];

export function SourceReadiness({
  kind = "source",
  id,
  onUpdated,
  onChange,
}: {
  kind?: SourcePipelineKind;
  id: string;
  onUpdated?: () => void;
  onChange?: (detail: SourcePipelineDetail | undefined) => void;
}) {
  const admin = useAdmin();
  const seconds = useBoardRefreshInterval();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const load = useCallback(
    (signal: AbortSignal) => getSourcePipelineDetail(kind, id, signal),
    [kind, id],
  );
  const result = useRequest(`source-pipeline-detail/${kind}/${id}`, load, seconds * 1000, revision);
  const data = result.data;
  useEffect(() => onChange?.(result.error ? undefined : data), [data, result.error, onChange]);
  async function run(target?: RetryTarget) {
    const item = data?.item;
    if (!item || pending.current || (!target && !item.can_advance)) return;
    if (
      target &&
      !item.retry_targets.some(
        (value) => value.stage === target.stage && value.job_id === target.job_id,
      )
    )
      return;
    pending.current = true;
    setBusy(true);
    try {
      if (target) await retrySourcePipeline(item, target, admin.csrf_token);
      else await advanceSourcePipeline(item, admin.csrf_token);
      notify.success(
        target ? `${sourcePipelineLabels[target.stage]} retry queued` : "Source processing updated",
      );
    } catch (cause) {
      notifyFailure(cause, "Could not update source processing");
    } finally {
      pending.current = false;
      setBusy(false);
      setRevision((value) => value + 1);
      onUpdated?.();
    }
  }
  return (
    <ResourcePanel title="Source readiness">
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      {data && (
        <div className="space-y-5 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="font-medium">{sourcePipelineLabels[data.item.stage]}</span>
              <StatusBadge value={data.item.state} />
            </div>
            <div className="flex flex-wrap gap-2">
              {data.item.kind === "source" && data.item.can_approve && (
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/content/sources/${data.item.id}/review`}>Review source</Link>
                </Button>
              )}
              {data.item.can_advance && (
                <Button size="sm" disabled={busy} loading={busy} onClick={() => void run()}>
                  {data.item.advance_label || "Run next step"}
                </Button>
              )}
              {!!data.item.retry_targets.length && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" disabled={busy}>
                      Retry processing
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {data.item.retry_targets.map((target) => (
                      <DropdownMenuItem key={target.stage} onSelect={() => void run(target)}>
                        {sourceRetryLabel(data.item, target.stage)}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          </div>
          {!!data.blockers.length && (
            <ul
              aria-label="Source blockers"
              className="space-y-1 rounded-md border border-amber-200 bg-amber-50/50 p-3 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200"
            >
              {data.blockers.map((blocker) => (
                <li key={blocker}>{humanize(blocker)}</li>
              ))}
            </ul>
          )}
          {!!data.checks.length && (
            <ul aria-label="Source readiness checks" className="divide-y rounded-md border px-3">
              {data.checks.map((check) => (
                <li
                  key={check.key}
                  className="flex flex-wrap items-start justify-between gap-2 py-3"
                >
                  <span className="font-medium">{humanize(check.key)}</span>
                  <StatusBadge value={check.status} />
                  {check.error && (
                    <p className="w-full break-words text-xs text-muted-foreground">
                      {check.error}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {!!data.tasks.length && (
            <div>
              <h3 className="mb-2 font-medium">Processing runs</h3>
              <ol aria-label="Source processing runs" className="divide-y rounded-md border px-3">
                {data.tasks.map((task, index) => {
                  const href = task.job_id ? sourcePipelineJobHref(task.stage, task.job_id) : null;
                  return (
                    <li key={`${task.stage}/${task.job_id ?? index}`} className="space-y-2 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium">{sourcePipelineLabels[task.stage]}</span>
                        <StatusBadge value={task.status} />
                      </div>
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        {href && (
                          <Link href={href} className="underline underline-offset-2">
                            Open run
                          </Link>
                        )}
                        <span>{task.attempts ? `Attempt ${task.attempts}` : "Not started"}</span>
                        {task.available_at &&
                          (task.status === "queued" || task.status === "retry_wait") && (
                            <span>
                              Retry available <DateTime value={task.available_at} />
                            </span>
                          )}
                        {task.finished_at && (
                          <span>
                            Finished <DateTime value={task.finished_at} />
                          </span>
                        )}
                      </div>
                      {task.error && (
                        <p className="break-words text-xs text-muted-foreground">{task.error}</p>
                      )}
                    </li>
                  );
                })}
              </ol>
            </div>
          )}
          {kind === "candidate" && data.profile.description && (
            <p className="whitespace-pre-wrap break-words text-muted-foreground">
              {data.profile.description}
            </p>
          )}
          {kind === "candidate" && data.assessment && (
            <details>
              <summary className="cursor-pointer font-medium">Assessment evidence</summary>
              <div className="mt-3">
                <SourceRelevanceAssessment
                  assessment={data.assessment}
                  approvalStatus={data.item.approval_status}
                />
              </div>
            </details>
          )}
          {kind === "candidate" && data.item.source_id && (
            <Button variant="outline" size="sm" asChild>
              <Link href={`/content/sources/${data.item.source_id}`}>Open source</Link>
            </Button>
          )}
          {data.item.stage === "active" && (
            <p className="text-xs text-muted-foreground">
              Feed ingestion follows the source schedule.
            </p>
          )}
          {data.item.stage === "paused" && (
            <p className="text-xs text-muted-foreground">
              New feed ingestion is paused. Existing articles remain available.
            </p>
          )}
          {data.item.stage === "rejected" && (
            <p className="text-xs text-muted-foreground">
              This source has been rejected. Review its decision before resuming processing.
            </p>
          )}
        </div>
      )}
    </ResourcePanel>
  );
}
