import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { humanize } from "@/lib/resources";
import type { AnalysisStepOut } from "@/lib/api/generated/models";

export function AnalysisStepList({
  steps,
  jobStatus,
}: {
  steps: AnalysisStepOut[];
  jobStatus?: string;
}) {
  if (!steps.length) return null;
  const current =
    steps.find((step) => step.status === "running") ??
    steps.find((step) => step.status === "failed") ??
    steps.find((step) => step.status === "pending");
  return (
    <div className="mt-3 space-y-2">
      {current && <p className="text-xs font-medium">Current step: {current.label}</p>}
      <ol className="divide-y rounded-md border px-3" aria-label="AI analysis steps">
        {steps.map((step) => {
          const retrying =
            jobStatus === "queued" &&
            step.attempts > 0 &&
            (step.status === "failed" || step.status === "pending");
          return (
            <li
              key={step.name}
              className="space-y-1.5 py-2.5"
              aria-current={current?.name === step.name ? "step" : undefined}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-medium">{step.label}</span>
                <StatusBadge
                  value={step.status}
                  label={
                    retrying
                      ? "Waiting to retry"
                      : step.status === "pending"
                        ? "Waiting"
                        : step.status === "succeeded"
                          ? "Complete"
                          : undefined
                  }
                  tone={retrying ? "warning" : undefined}
                />
              </div>
              <p className="break-words text-[11px] text-muted-foreground">
                Prompt {step.prompt_version} · {step.attempts} attempt
                {step.attempts === 1 ? "" : "s"}
              </p>
              {retrying && step.available_at && (
                <p className="text-[11px] text-muted-foreground">
                  Retry available <DateTime value={step.available_at} />
                </p>
              )}
              {step.finished_at && (
                <p className="text-[11px] text-muted-foreground">
                  {step.status === "failed" || retrying ? "Attempt finished" : "Finished"}{" "}
                  <DateTime value={step.finished_at} />
                </p>
              )}
              {step.error && (step.status === "failed" || retrying) && (
                <p className="whitespace-pre-wrap break-words text-xs text-destructive">
                  {humanize(step.error)}
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
