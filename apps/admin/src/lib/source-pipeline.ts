import {
  adminSourcePipeline,
  adminSourcePipelineAdvance,
  adminSourcePipelineColumn,
  adminSourcePipelineDetail,
  adminSourcePipelineRetry,
} from "./api/generated/admin";
import type { AdminSourcePipelineParams, SourcePipelineCard } from "./api/generated/models";
import { recordHref } from "./routes";

export type {
  SourcePipelineBoard,
  SourcePipelineColumn,
  SourcePipelineDetail,
  SourcePipelineCard,
} from "./api/generated/models";
export const sourcePipelineStages = [
  "discovery",
  "profile",
  "assessment",
  "branding",
  "review",
  "ingestion",
  "active",
  "paused",
  "rejected",
] as const;
export type SourcePipelineStage = (typeof sourcePipelineStages)[number];
export type SourcePipelineKind = "source" | "candidate";
export const sourcePipelineLabels: Record<SourcePipelineStage, string> = {
  discovery: "Discovery",
  profile: "Profile",
  assessment: "AI assessment",
  branding: "Branding",
  review: "Review",
  ingestion: "Ingestion",
  active: "Active",
  paused: "Paused",
  rejected: "Rejected",
};
export const sourceColumnsStorageKey = "devfeed:admin-source-pipeline-columns";

export function sourcePipelineParams(query: string): AdminSourcePipelineParams {
  const search = new URLSearchParams(query);
  const sourceType = search.get("source_type");
  return {
    limit: 20,
    ...(search.get("q") ? { q: search.get("q")! } : {}),
    ...(sourceType === "publisher" || sourceType === "aggregator"
      ? { source_type: sourceType }
      : {}),
    ...(search.get("needs_attention") === "true" ? { needs_attention: true } : {}),
    ...(search.get("terminal_since") ? { terminal_since: search.get("terminal_since")! } : {}),
  };
}
export function getSourcePipeline(query: string, signal: AbortSignal) {
  return adminSourcePipeline(sourcePipelineParams(query), { signal });
}
export function getSourcePipelineColumn(
  stage: SourcePipelineStage,
  query: string,
  cursor: number,
  signal: AbortSignal,
) {
  return adminSourcePipelineColumn(stage, { ...sourcePipelineParams(query), cursor }, { signal });
}
export function getSourcePipelineDetail(kind: SourcePipelineKind, id: string, signal: AbortSignal) {
  return adminSourcePipelineDetail(kind, id, { signal });
}
export function advanceSourcePipeline(item: SourcePipelineCard, csrf: string) {
  return adminSourcePipelineAdvance(
    item.kind,
    item.id,
    { expected_snapshot: item.snapshot },
    { headers: { "X-CSRF-Token": csrf } },
  );
}
export function retrySourcePipeline(
  item: SourcePipelineCard,
  target: SourcePipelineCard["retry_targets"][number],
  csrf: string,
) {
  return adminSourcePipelineRetry(
    item.kind,
    item.id,
    { expected_snapshot: item.snapshot, target: target.stage, job_id: target.job_id },
    { headers: { "X-CSRF-Token": csrf } },
  );
}
export function sourcePipelineHost(item: Pick<SourcePipelineCard, "website_url" | "feed_url">) {
  for (const value of [item.website_url, item.feed_url]) {
    if (!value) continue;
    try {
      const url = new URL(value);
      if (url.protocol === "https:" || url.protocol === "http:") return url.host;
    } catch {
      /* Source discovery may not have found a valid URL yet. */
    }
  }
  return "Source candidate";
}
export function sourcePipelineJobHref(stage: SourcePipelineStage, id: string) {
  const resource =
    stage === "ingestion"
      ? "ingestion-jobs"
      : stage === "branding"
        ? "image-jobs"
        : stage === "profile" || stage === "assessment"
          ? "source-jobs"
          : null;
  return resource ? recordHref({ resource, id }) : null;
}
export function sourceRetryLabel(item: SourcePipelineCard, target: SourcePipelineStage) {
  const pauses = item.enabled && item.approval_status === "approved" && target !== "ingestion";
  return `${pauses ? "Pause and retry" : "Retry"} ${sourcePipelineLabels[target].toLowerCase()}`;
}
