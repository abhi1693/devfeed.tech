import {
  adminArticlePipeline,
  adminArticlePipelineAdvance,
  adminArticlePipelineColumn,
  adminArticlePipelineDetail,
  adminArticlePipelineRetry,
  adminArticleMaintenanceReview,
  adminArticleMaintenanceRetry,
} from "./api/generated/admin";
import type {
  AdminArticlePipelineParams,
  PipelineCard,
  ReviewMaintenance,
} from "./api/generated/models";
import { recordHref } from "./routes";

export type {
  PipelineBoard,
  PipelineColumn,
  PipelineDetail,
  PipelineJob,
  PipelineJobs,
  PipelineSourceGroup,
} from "./api/generated/models";
export type PipelineArticle = PipelineCard;
export const pipelineColumnsStorageKey = "devfeed:admin-article-pipeline-columns";
export const pipelineStages = [
  "ingested",
  "enrichment",
  "analysis",
  "images",
  "review",
  "ready",
  "published",
  "rejected",
] as const;
export type PipelineStage = (typeof pipelineStages)[number];
export type PipelineRetryTarget = Extract<PipelineStage, "enrichment" | "analysis" | "images">;
export type PipelineJobKey = "enrichment" | "analysis" | "images";
export const pipelineLabels: Record<PipelineStage, string> = {
  ingested: "Ingested",
  enrichment: "Enrichment",
  analysis: "AI analysis",
  images: "Images",
  review: "Review",
  ready: "Ready",
  published: "Published",
  rejected: "Rejected",
};

const analysisStepLabels: Record<string, string> = {
  developer_relevance: "Developer relevance",
  page_kind: "Article eligibility",
  language: "Source language",
  content_type: "Content type",
  content_format: "Content format",
  topics: "Topic matching",
  tags: "Tag matching",
  identifying_title: "Identifying title",
  overview: "Article overview",
  description: "Search description",
};

export function analysisStepLabel(name: string) {
  return analysisStepLabels[name] ?? name.replaceAll("_", " ");
}

export function articlePipelineParams(query: string): AdminArticlePipelineParams {
  const params = new URLSearchParams(query);
  return {
    limit: 20,
    ...(params.get("q") ? { q: params.get("q")! } : {}),
    ...(params.get("source_id") ? { source_id: params.get("source_id")! } : {}),
    ...(params.get("needs_attention") === "true" ? { needs_attention: true } : {}),
    ...(params.get("group_by") === "source" ? { group_by: "source" as const } : {}),
  };
}
export function getArticlePipeline(query: string, signal: AbortSignal) {
  return adminArticlePipeline(articlePipelineParams(query), { signal });
}
export function getArticlePipelineColumn(
  stage: PipelineStage,
  query: string,
  cursor: number,
  signal: AbortSignal,
) {
  return adminArticlePipelineColumn(stage, { ...articlePipelineParams(query), cursor }, { signal });
}
export function getArticlePipelineDetail(id: string, signal: AbortSignal) {
  return adminArticlePipelineDetail(id, { signal });
}
export function getArticlePipelineSourceArticles(
  stage: PipelineStage,
  query: string,
  source_id: string | null,
  cursor: number,
  signal: AbortSignal,
) {
  const params = articlePipelineParams(query);
  delete params.group_by;
  return adminArticlePipelineColumn(
    stage,
    { ...params, source_group: source_id ?? "unknown", cursor },
    { signal },
  );
}
export function advanceArticlePipeline(id: string, expected_revision: number, csrf: string) {
  return adminArticlePipelineAdvance(
    id,
    { expected_revision },
    { headers: { "X-CSRF-Token": csrf } },
  );
}
export function retryArticlePipeline(
  id: string,
  target: PipelineRetryTarget,
  expected_revision: number,
  csrf: string,
  expected_job_id: string | null,
) {
  return adminArticlePipelineRetry(
    id,
    { target, expected_revision, expected_job_id },
    { headers: { "X-CSRF-Token": csrf } },
  );
}
export function reviewArticleMaintenance(
  id: string,
  refreshId: string,
  decision: ReviewMaintenance,
  csrf: string,
) {
  return adminArticleMaintenanceReview(id, refreshId, decision, {
    headers: { "X-CSRF-Token": csrf },
  });
}
export function retryArticleMaintenance(
  id: string,
  target: PipelineRetryTarget,
  expected_revision: number,
  csrf: string,
  expected_job_id: string | null,
) {
  return adminArticleMaintenanceRetry(
    id,
    { target, expected_revision, expected_job_id },
    { headers: { "X-CSRF-Token": csrf } },
  );
}
export function pipelineJobHref(key: PipelineJobKey, id: string) {
  return recordHref(
    key === "analysis"
      ? { resource: "analysis-jobs", kind: "analysis", id }
      : { resource: key === "enrichment" ? "article-jobs" : "image-jobs", id },
  );
}
