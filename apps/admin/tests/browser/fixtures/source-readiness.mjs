import assert from "node:assert/strict";

/** Authoritative readiness for approved, enabled source-detail browser fixtures. */
export function activeSourceReadiness(source) {
  assert.equal(source.approval_status, "approved");
  assert.equal(source.enabled, true);
  return {
    item: {
      key: `source:${source.id}`,
      kind: "source",
      id: source.id,
      source_id: source.id,
      candidate_id: null,
      name: source.name,
      website_url: source.website_url ?? null,
      feed_url: source.feed_url,
      source_type: source.source_type,
      approval_status: source.approval_status,
      enabled: source.enabled,
      stage: "active",
      state: "complete",
      created_at: source.created_at ?? "2026-10-19T09:00:00Z",
      updated_at: source.updated_at ?? "2026-10-19T09:00:00Z",
      snapshot: "a".repeat(64),
      can_advance: false,
      can_approve: false,
      can_ingest: true,
      next_action: null,
      advance_label: null,
      retry_targets: [],
    },
    checks: [],
    blockers: [],
    tasks: [],
    profile: {
      description: source.description ?? null,
      website_url: source.website_url ?? null,
      logo_url: source.logo_url ?? null,
      image_url: source.image_url ?? null,
      language: source.language ?? null,
    },
    assessment: source.relevance_assessment ?? null,
    reviews: [],
    candidate_ids: [],
  };
}
