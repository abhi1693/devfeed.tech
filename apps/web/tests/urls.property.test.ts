import fc from "fast-check";
import { expect, it } from "vitest";
import {
  contentTypes,
  feedHref,
  feedParams,
  outboundArticleUrl,
  parseFilters,
  personalFeedHref,
  safeExternalUrl,
} from "@/lib/feed-query";
import { propertyOptions } from "../../../scripts/ci/property-config.mjs";

// Printable graphemes include combining marks and non-Latin scripts; binary
// strings also exercise control characters and supplementary code points.
const unicode = fc.string({ unit: "grapheme", maxLength: 20 });
const rawUrl = fc.oneof(
  fc.string({ unit: "binary", maxLength: 200 }),
  fc.webUrl(),
  fc
    .tuple(
      fc.constantFrom(
        "javascript:",
        "data:",
        "file:",
        "https://user:pass@example.test/",
        "//example.test/",
      ),
      unicode,
    )
    .map(([prefix, value]) => prefix + value),
);

it("only accepts credential-free HTTP URLs and normalizes them idempotently", () => {
  fc.assert(
    fc.property(rawUrl, (input) => {
      const result = safeExternalUrl(input);
      if (result === undefined) return;
      const url = new URL(result);
      expect(["http:", "https:"]).toContain(url.protocol);
      expect(url.username).toBe("");
      expect(url.password).toBe("");
      expect(safeExternalUrl(result)).toBe(result);
      expect(outboundArticleUrl(result)).toBe(outboundArticleUrl(outboundArticleUrl(result)!));
    }),
    propertyOptions(),
  );
});

it("rejects arbitrary credentials and unsafe URL schemes", () => {
  fc.assert(
    fc.property(unicode, fc.constantFrom("javascript", "data", "file", "ftp"), (value, scheme) => {
      const url = new URL("https://publisher.test/article");
      url.username = "reader" + value;
      url.password = value;
      expect(safeExternalUrl(url.href)).toBeUndefined();
      expect(outboundArticleUrl(url.href)).toBeUndefined();
      expect(safeExternalUrl(`${scheme}:${value}`)).toBeUndefined();
    }),
    propertyOptions(),
  );
});

it("preserves Unicode URL paths, fragments and repeated query parameters when attributing links", () => {
  fc.assert(
    fc.property(
      unicode,
      fc.array(fc.tuple(unicode, unicode), { maxLength: 12 }),
      unicode,
      (path, entries, hash) => {
        const input = new URL(`https://publisher.test/${encodeURIComponent(path)}`);
        input.search = new URLSearchParams(entries).toString();
        input.hash = hash;
        const output = new URL(outboundArticleUrl(input.href)!);
        expect(output.origin).toBe(input.origin);
        expect(output.pathname).toBe(input.pathname);
        expect(output.hash).toBe(input.hash);
        expect(output.searchParams.getAll("utm_source")).toEqual(["devfeed"]);
        expect([...output.searchParams].filter(([key]) => key !== "utm_source")).toEqual(
          entries.filter(([key]) => key !== "utm_source"),
        );
      },
    ),
    propertyOptions(),
  );
});

it("round-trips bounded Unicode filters without leaking routing hints or old cursors", () => {
  fc.assert(
    fc.property(
      unicode,
      unicode,
      fc.constantFrom(...contentTypes),
      fc.uuid(),
      (query, tag, type, source) => {
        const filters = parseFilters({
          q: query,
          tag,
          content_type: type,
          source_id: source,
          cursor: "old+/=",
        });
        filters.source_slug = "resolved-source";
        const serialized = feedParams(filters);
        expect(serialized.has("source_slug")).toBe(false);
        expect(parseFilters(Object.fromEntries(serialized))).toEqual(parseFilters({ ...filters }));
        const personal = new URL(personalFeedHref(filters), "https://devfeed.test");
        const latest = new URL(feedHref(filters), "https://devfeed.test");
        expect(personal.searchParams.has("cursor")).toBe(false);
        expect(latest.searchParams.has("cursor")).toBe(false);
        expect(personal.searchParams.get("q") ?? "").toBe(filters.q);
        expect(latest.searchParams.get("q") ?? "").toBe(filters.q);
        expect(latest.pathname).toBe(
          `/sources/resolved-source/${type === "article" ? "articles" : type === "tutorial" ? "tutorials" : type === "release" ? "releases" : type === "comparison" ? "comparisons" : type === "opinion" ? "opinions" : "news"}`,
        );
      },
    ),
    propertyOptions(),
  );
});
