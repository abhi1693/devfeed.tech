import assert from "node:assert/strict";

export function publicPlacementResponse(request) {
  assert.equal(request.method, "POST", "Public placement queries must use POST");
  for (const name of ["cookie", "authorization", "x-csrf-token"])
    assert.equal(request.headers[name], undefined, `Public placements must omit ${name}`);
  return { items: [] };
}

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
