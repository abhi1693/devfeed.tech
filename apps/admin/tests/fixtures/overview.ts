import type { AdminOverview } from "@/lib/api/generated/models";

export const emptyOverview: AdminOverview = {
  generated_at: "2026-09-09T12:00:00Z", days: 30,
  articles: 0, articles_pending_review: 0, articles_published: 0,
  sources: 0, sources_pending_review: 0, sources_active: 0, sources_failing: 0,
  topics: 0, topics_active: 0, topic_proposals_pending: 0, relationship_proposals_pending: 0,
  activity: [], analysis_activity: [], top_topics: [], analysis: { queued: 0, running: 0, succeeded: 0, failed: 0 },
};
export const populatedOverview: AdminOverview = {
  ...emptyOverview, articles: 1248, articles_published: 832, articles_pending_review: 216,
  sources: 48, sources_active: 42, sources_pending_review: 3, sources_failing: 1,
  topics: 64, topics_active: 58, topic_proposals_pending: 24, relationship_proposals_pending: 6,
  analysis: { queued: 18, running: 2, succeeded: 364, failed: 3 },
  activity: [{ date: "2026-09-08", added: 42, published: 27 }, { date: "2026-09-09", added: 18, published: 12 }],
  analysis_activity: [{ date: "2026-09-08", succeeded: 180, failed: 2 }, { date: "2026-09-09", succeeded: 184, failed: 1 }],
  top_topics: [{ id: "topic-1", name: "TypeScript", articles: 124 }, { id: "topic-2", name: "Artificial intelligence", articles: 98 }],
};
