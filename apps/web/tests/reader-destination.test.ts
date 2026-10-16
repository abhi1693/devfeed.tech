import { expect, it } from "vitest";
import {
  articleDestination,
  pageDestination,
  resolveReaderDestination,
} from "@/lib/reader-destination";

it("declares article identity and preserves reading position", () => {
  expect(articleDestination("rust-traits")).toEqual({
    kind: "article",
    slug: "rust-traits",
    href: "/articles/rust-traits",
    scroll: "preserve",
  });
  expect(() => articleDestination("rust/traits")).toThrow(TypeError);
  expect(() => pageDestination("/articles/rust-traits")).toThrow(TypeError);
});

it("decodes a generic article link once without discarding query or anchor", () => {
  expect(resolveReaderDestination("/articles/%72ust?from=inbox#summary")).toEqual({
    kind: "article",
    slug: "rust",
    href: "/articles/%72ust?from=inbox#summary",
    scroll: "preserve",
  });
  expect(resolveReaderDestination("/articles/%2572ust")).toMatchObject({
    kind: "page",
    route: null,
  });
});

it("retains page descriptors and explicit scroll intent", () => {
  expect(pageDestination("/topics/rust/tutorials?tag=backend", "preserve")).toMatchObject({
    kind: "page",
    href: "/topics/rust/tutorials?tag=backend",
    scroll: "preserve",
    route: { type: "reader", detail: { kind: "topics", slug: "rust", contentType: "tutorial" } },
  });
  expect(pageDestination("/settings/notifications")).toMatchObject({
    kind: "page",
    scroll: "reset",
    route: { type: "local", page: "settings", settings: "notifications" },
  });
  expect(pageDestination("/missing")).toMatchObject({ kind: "page", route: null });
});

it("keeps another origin's article paths external and rejects executable URLs", () => {
  expect(
    resolveReaderDestination("https://elsewhere.test/articles/rust", {
      base: "https://devfeed.tech/latest",
    }),
  ).toMatchObject({
    kind: "page",
    href: "https://elsewhere.test/articles/rust",
    route: null,
  });
  for (const href of ["javascript:alert(1)", "data:text/html,hi"]) {
    expect(() => resolveReaderDestination(href)).toThrow(TypeError);
  }
});
