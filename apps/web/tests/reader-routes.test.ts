import { describe, expect, it } from "vitest";
import {
  collectionRoute,
  readerRouteLabels,
  readerScreenNames,
  resolveReaderRoute,
} from "@/lib/reader-routes";
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
    "/legal/privacy",
  ])("leaves unsupported reader path %s unresolved", (path) => {
    expect(resolveReaderRoute(path)).toBeNull();
  });
});

describe("privacy-safe reader route labels", () => {
  it.each([
    ["/", "/", "/"],
    ["/latest", "/latest", "/latest"],
    ["/search", "/search", "/search"],
    ["/read-later", "/read-later", "/read-later"],
    ["/trending", "/trending", "/trending"],
    ["/topics", "/topics", "/topics"],
    ["/sources", "/sources", "/sources"],
    ["/sources/suggest", "/sources/suggest", "/sources/suggest"],
    ["/mcp", "/mcp", "/mcp"],
    ["/mcp/authorize", "/mcp/authorize", "/mcp/authorize"],
    ["/leaderboard", "/leaderboard", "/leaderboard"],
    ["/dev-card", "/dev-card", "/dev-card"],
    ["/settings", "/settings/profile", "/settings/profile"],
    ["/settings/profile", "/settings/profile", "/settings/profile"],
    ["/settings/appearance", "/settings/appearance", "/settings/appearance"],
    ["/settings/feed", "/settings/feed", "/settings/feed"],
    ["/settings/notifications", "/settings/notifications", "/settings/notifications"],
    ["/settings/topics", "/settings/topics", "/settings/topics"],
    ["/settings/sources", "/settings/sources", "/settings/sources"],
    ["/articles/private-article", "/articles/:id", "/articles"],
    ["/articles/%70rivate-article/", "/articles/:id", "/articles"],
    ["/users/private_username", "/users/:username", "/users/profile"],
    ["/topics/%70rivate-topic", "/topics/:id", "/topics/detail"],
    ["/topics/private-topic/tutorials", "/topics/:id/:type", "/topics/detail"],
    ["/sources/private-source", "/sources/:id", "/sources/detail"],
    ["/sources/private-source/news", "/sources/:id/:type", "/sources/detail"],
    ["/articles", "/articles", "/articles"],
    ["/news", "/news", "/news"],
    ["/tutorials", "/tutorials", "/tutorials"],
    ["/releases", "/releases", "/releases"],
    ["/comparisons", "/comparisons", "/comparisons"],
    ["/opinions", "/opinions", "/opinions"],
  ])("projects %s through its resolved route", (path, telemetry, screen) => {
    expect(readerRouteLabels(resolveReaderRoute(path))).toEqual({ telemetry, screen });
    expect(readerScreenNames).toContain(screen);
    expect(`${telemetry} ${screen}`).not.toContain("private");
  });

  it.each([
    "/topics/private-topic/unknown",
    "/sources/private-source/unknown",
    "/topics/private-topic/news/extra",
    "/users/private_username/settings",
    "/mcp/authorize/private-client",
    "/settings/private-field",
    "/search?q=private@example.test",
    "/articles/%2Fsecret",
    "/users/%2570rivate_username",
    "/topics/%GG",
  ])("does not classify invalid route %s by a familiar prefix", (path) => {
    expect(readerRouteLabels(resolveReaderRoute(path))).toBeNull();
  });

  it("projects semantic descriptors without retaining identity fields", () => {
    expect(readerRouteLabels({ type: "article", slug: "private@example.test" })).toEqual({
      telemetry: "/articles/:id",
      screen: "/articles",
    });
    expect(readerRouteLabels({ type: "profile", username: "private@example.test" })).toEqual({
      telemetry: "/users/:username",
      screen: "/users/profile",
    });
    expect(
      readerRouteLabels({
        type: "reader",
        feed: readerFeeds.contextual,
        detail: { kind: "topics", slug: "private@example.test", contentType: "opinion" },
      }),
    ).toEqual({ telemetry: "/topics/:id/:type", screen: "/topics/detail" });
  });
});
