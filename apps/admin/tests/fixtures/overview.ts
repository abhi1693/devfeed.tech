import type { AdminOverview } from "@/lib/api/generated/models";

export const emptyOverview: AdminOverview = {
  generated_at: "2026-09-09T12:00:00Z", days: 30,
  articles: 0, articles_pending_review: 0, articles_published: 0,
  sources: 0, sources_pending_review: 0, sources_active: 0, sources_failing: 0,
  topics: 0, topics_active: 0, topic_proposals_pending: 0, relationship_proposals_pending: 0,
  activity: [], analysis_activity: [], top_topics: [], analysis: { queued: 0, running: 0, succeeded: 0, failed: 0 },
  insights: { publications: { current: 0, previous: 0 }, opens: { current: 0, previous: null }, accounts: { current: 0, previous: 0 }, publication_seconds: { current: null, previous: null }, reader_activity: [], top_articles_days: 30, personalization: {}, processing: [] },
};
export const populatedOverview: AdminOverview = {
  ...emptyOverview, articles: 1248, articles_published: 832, articles_pending_review: 216,
  sources: 48, sources_active: 42, sources_pending_review: 3, sources_failing: 1,
  topics: 64, topics_active: 58, topic_proposals_pending: 24, relationship_proposals_pending: 6,
  analysis: { queued: 18, running: 2, succeeded: 364, failed: 3 },
  activity: [{ date: "2026-09-08", added: 42, published: 27 }, { date: "2026-09-09", added: 18, published: 12 }],
  analysis_activity: [{ date: "2026-09-08", succeeded: 180, failed: 2 }, { date: "2026-09-09", succeeded: 184, failed: 1 }],
  top_topics: [{ id: "topic-1", name: "TypeScript", articles: 124 }, { id: "topic-2", name: "Artificial intelligence", articles: 98 }],
  insights: {
    ...emptyOverview.insights,
    publications: { current: 39, previous: 30 }, opens: { current: 320, previous: 200 }, accounts: { current: 12, previous: 8 }, publication_seconds: { current: 3600, previous: 7200 }, publication_p90_seconds: 14400,
    reader_activity: [{ date: "2026-09-08", added: 42, published: 27, opens: 200, accounts: 8, content_types: { article: 20, tutorial: 7 }, median_publication_seconds: 3000 }, { date: "2026-09-09", added: 18, published: 12, opens: 120, accounts: 4, content_types: { article: 12 }, median_publication_seconds: 4000 }],
    personalization: { ready: 10, pending: 2, refreshing: 1, not_needed: 3, overdue: 1, empty_with_interests: 1, issues: [{ id: "user-1", name: "Ada", issue: "Refresh overdue", next_refresh_at: "2026-09-08T12:00:00Z" }], reasons: [{ reason: "followed_source", users: 4, recommendations: 20 }] },
    top_articles: [{ id: "article-1", title: "TypeScript guide", opens: 20, likes: 5 }],
    coverage: [{ id: "topic-1", name: "Python", logo_url: null, followers: 8, inferred_users: 3, publications: 0, last_published_at: null }],
    source_performance: [{ id: "source-1", name: "Example publisher", logo_url: null, discovered: 20, published: 15, followers: 5, fetch_failures: 1, consecutive_failures: 1, last_success_at: null }],
    processing: [{ kind: "analysis", queued: 18, running: 2, completed: 364, failed: 3, oldest_queued_at: "2026-09-08T12:00:00Z" }],
  },
};
