import {
  sourcePipelineStages,
  type SourcePipelineBoard,
  type SourcePipelineCard,
  type SourcePipelineDetail,
} from "@/lib/source-pipeline";

export const item: SourcePipelineCard = {
  key: "source:source-one",
  kind: "source",
  id: "source-one",
  source_id: "source-one",
  candidate_id: null,
  name: "Engineering Journal",
  website_url: "https://engineering.example/",
  feed_url: "https://engineering.example/feed.xml",
  source_type: "publisher",
  approval_status: "pending",
  enabled: false,
  stage: "profile",
  state: "running",
  created_at: "2026-10-19T08:00:00.000001Z",
  updated_at: "2026-10-19T08:03:00Z",
  snapshot: "a".repeat(64),
  can_advance: false,
  can_approve: false,
  can_ingest: false,
  next_action: null,
  advance_label: null,
  retry_targets: [],
};
export function board(card = item): SourcePipelineBoard {
  return {
    as_of: "2026-10-19T08:03:00Z",
    terminal_since: "2026-10-12T08:03:00Z",
    counts: Object.fromEntries(
      sourcePipelineStages.map((stage) => [stage, stage === card.stage ? 1 : 0]),
    ),
    columns: sourcePipelineStages.map((stage) => ({
      stage,
      total: stage === card.stage ? 1 : 0,
      items: stage === card.stage ? [card] : [],
      next_cursor: null,
    })),
  };
}
export function detail(card = item): SourcePipelineDetail {
  return {
    item: card,
    checks: [
      { key: "profile", status: "running", job_id: "profile-job", error: null, available_at: null },
    ],
    blockers: ["profile_processing"],
    tasks: [
      {
        stage: "profile",
        job_id: "profile-job",
        status: "running",
        attempts: 1,
        created_at: item.created_at,
        available_at: item.created_at,
        finished_at: null,
        error: null,
      },
    ],
    profile: {
      description: "Practical engineering articles.",
      website_url: item.website_url,
      logo_url: null,
      image_url: null,
      language: "en",
    },
    assessment: null,
    reviews: [],
    candidate_ids: [],
  };
}
