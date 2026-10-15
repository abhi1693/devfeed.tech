import { describe, expect, it } from "vitest";
import { collectionRoute, resolveReaderRoute } from "@/lib/reader-routes";
import { readerFeeds } from "@/lib/reader-feed";

describe("shared reader route boundaries", () => {
  it.each(["c++", "c#", ".net", "日本語", "two words", "%2F"])(
    "preserves technology tag %s without decoding an already decoded parameter",
    (slug) => {
      expect(collectionRoute("tags", slug, "tutorials")).toEqual({
        kind: "tags",
        slug,
        contentType: "tutorial",
      });
    },
  );

  it("decodes dynamic topic names exactly once", () => {
    expect(resolveReaderRoute("/topics/%72ust/tutorials")).toEqual({
      type: "reader",
      feed: readerFeeds.contextual,
      detail: { kind: "topics", slug: "rust", contentType: "tutorial" },
    });
    expect(resolveReaderRoute("/topics/%2572ust/tutorials")).toBeNull();
  });

  it.each(["%", "%ZZ", "%2F", "%5C", "%00", "%7F", "%ED%A0%80", "c%2B%2B", "c%23", "bad%20slug"])(
    "rejects malformed or unsafe encoded topic %s",
    (slug) => expect(resolveReaderRoute(`/topics/${slug}/news`)).toBeNull(),
  );

  it("distinguishes missing suffixes from invalid supplied suffixes", () => {
    expect(collectionRoute("topics", "rust")).toEqual({ kind: "topics", slug: "rust" });
    for (const suffix of ["", "unknown", "article", "NEWS", "news/extra"])
      expect(collectionRoute("topics", "rust", suffix)).toBeNull();
    expect(resolveReaderRoute("/topics/rust/")).toEqual(resolveReaderRoute("/topics/rust"));
    expect(resolveReaderRoute("/topics/rust//")).toBeNull();
  });

  it("preserves source identifiers and bounds collection names", () => {
    const id = "A0C3ED98-D4C2-4A59-9106-267D9A8FD68A";
    expect(resolveReaderRoute(`/sources/${id}/news`)).toMatchObject({ detail: { slug: id } });
    expect(collectionRoute("sources", "a".repeat(200))).not.toBeNull();
    expect(collectionRoute("sources", "a".repeat(201))).toBeNull();
    expect(collectionRoute("topics", "a".repeat(100))).not.toBeNull();
    expect(collectionRoute("topics", "a".repeat(101))).toBeNull();
    expect(collectionRoute("tags", "\ud800")).toBeNull();
    expect(collectionRoute("sources", "c++")).toBeNull();
  });

  it.each([
    "/Topics/rust",
    "/%74opics/rust",
    "/topics/rust/news/extra",
    "/topics//news",
    "//topics/rust",
    "/latest?content_type=news",
    "/latest#news",
    "latest",
    "/settings/unknown",
    "/tags/c%2B%2B",
    "/legal/privacy",
  ])("leaves unsupported reader path %s unresolved", (path) => {
    expect(resolveReaderRoute(path)).toBeNull();
  });
});
