import {
  adminArticlePipeline,
  adminArticlePipelineAdvance,
  adminArticlePipelineColumn,
  adminArticlePipelineDetail,
} from "./api/generated/admin";
import type { AdminArticlePipelineParams, PipelineCard } from "./api/generated/models";
import { recordHref } from "./routes";

export type {
  PipelineBoard,
  PipelineColumn,
  PipelineDetail,
  PipelineJob,
  PipelineJobs,
} from "./api/generated/models";
export type PipelineArticle = PipelineCard;
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

export function pipelineQuery(query: string): URLSearchParams {
  const original = new URLSearchParams(query);
  const params = new URLSearchParams({ limit: "20" });
  for (const key of ["q", "source_id", "needs_attention"])
    if (original.get(key)) params.set(key, original.get(key)!);
  return params;
}
function pipelineParams(query: string): AdminArticlePipelineParams {
  const params = pipelineQuery(query);
  return {
    limit: 20,
    ...(params.get("q") ? { q: params.get("q")! } : {}),
    ...(params.get("source_id") ? { source_id: params.get("source_id")! } : {}),
    ...(params.get("needs_attention") === "true" ? { needs_attention: true } : {}),
  };
}
export function getArticlePipeline(query: string, signal: AbortSignal) {
  return adminArticlePipeline(pipelineParams(query), { signal });
}
export function getArticlePipelineColumn(
  stage: PipelineStage,
  query: string,
  cursor: number,
  signal: AbortSignal,
) {
  return adminArticlePipelineColumn(stage, { ...pipelineParams(query), cursor }, { signal });
}
export function getArticlePipelineDetail(id: string, signal: AbortSignal) {
  return adminArticlePipelineDetail(id, { signal });
}
export function advanceArticlePipeline(id: string, expected_revision: number, csrf: string) {
  return adminArticlePipelineAdvance(
    id,
    { expected_revision },
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
