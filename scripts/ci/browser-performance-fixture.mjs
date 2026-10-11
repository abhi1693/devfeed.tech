import assert from "node:assert/strict";

// Keep managed fixture images and IDs stable when comparing real article copy.
export function applyArticleSnapshot(fixture, snapshot, phase) {
  assert.ok(phase, "Article snapshots require --article-entry");
  assert.equal(typeof snapshot?.article?.slug, "string");
  assert.equal(typeof snapshot.article.title, "string");
  for (const key of [
    "slug",
    "title",
    "summary",
    "ai_summary",
    "ai_description",
    "author",
    "published_at",
    "feed_at",
    "content_type",
    "content_format",
    "language",
  ]) {
    if (key in snapshot.article) fixture.article[key] = snapshot.article[key];
  }
  return fixture;
}
