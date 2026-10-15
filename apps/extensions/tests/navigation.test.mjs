import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { build } from "esbuild";
import { JSDOM } from "jsdom";

const require = createRequire(import.meta.url);
const compiled = await build({
  entryPoints: [new URL("../src/navigation.tsx", import.meta.url).pathname],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
  external: ["react"],
});
const destinations = await build({
  entryPoints: [new URL("../../web/src/lib/reader-destination.ts", import.meta.url).pathname],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
});
function moduleIn(source, extra = {}) {
  const output = { exports: {} };
  runInNewContext(source, {
    module: output,
    exports: output.exports,
    require,
    URL,
    URLSearchParams,
    Request,
    Headers,
    Response,
    AbortSignal,
    ...extra,
  });
  return output.exports;
}
const { articleDestination, pageDestination } = moduleIn(destinations.outputFiles[0].text);
function browser(route = "/latest") {
  const dom = new JSDOM("", { url: `https://extension.invalid/newtab.html#${route}` });
  const { window } = dom;
  const calls = [];
  window.scrollTo = (x, y) => {
    calls.push([x, y]);
    window.scrollX = x;
    window.scrollY = y;
  };
  window.open = (...args) => calls.push(args);
  const navigation = moduleIn(compiled.outputFiles[0].text, {
    window,
    Event: window.Event,
    DOMException: window.DOMException,
  });
  return { dom, window, calls, ...navigation };
}
const plain = (value) => JSON.parse(JSON.stringify(value));

test("article destinations retain each page kind and its reading position", () => {
  for (const route of [
    "/latest?cursor=next",
    "/topics/rust/tutorials?tag=backend",
    "/search?q=rust",
    "/topics",
    "/settings/profile",
    "/users/reader",
  ]) {
    const { window, calls, navigate, dom } = browser(route);
    window.scrollX = 12;
    window.scrollY = 740;
    navigate(articleDestination("rust"));
    const entry = window.history.state;
    assert.equal(window.location.hash, "#/articles/rust");
    assert.equal(entry.readerNavigation.kind, "article");
    assert.equal(entry.readerNavigation.destination.slug, "rust");
    assert.equal(entry.readerNavigation.background.href, route);
    assert.deepEqual(plain(entry.readerScroll), [12, 740]);
    assert.deepEqual(calls, []);
    navigate(articleDestination("other"), "replace");
    assert.equal(window.history.state.readerNavigation.background.href, route);
    assert.equal(window.history.state.readerNavigation.destination.slug, "other");
    dom.window.close();
  }
});

test("direct and same-article navigation preserve direct entry instead of inventing a background", () => {
  const { window, calls, navigate, dom } = browser("/articles/rust");
  let refreshes = 0;
  window.addEventListener("devfeed:extension-refresh", () => refreshes++);
  navigate(articleDestination("rust"), "replace");
  assert.equal(refreshes, 1);
  assert.equal(window.history.state.readerNavigation.background, null);
  navigate(articleDestination("other"), "replace");
  assert.equal(window.history.state.readerNavigation.background, null);
  assert.deepEqual(calls, []);
  dom.window.close();
});

test("page scroll intent is explicit and article links preserve it even through the generic adapter", () => {
  const { window, calls, navigate, useRouter, dom } = browser();
  window.scrollY = 740;
  navigate(pageDestination("/search?q=rust", "preserve"));
  assert.deepEqual(calls, []);
  assert.deepEqual(plain(window.history.state.readerScroll), [0, 740]);
  useRouter().push("/articles/rust", { scroll: true });
  assert.deepEqual(calls, []);
  assert.equal(window.history.state.readerNavigation.background.href, "/search?q=rust");
  navigate(pageDestination("/settings/notifications"));
  assert.deepEqual(calls, [[0, 0]]);
  assert.deepEqual(plain(window.history.state.readerScroll), [0, 0]);
  assert.equal(window.history.state.readerNavigation.kind, "page");
  dom.window.close();
});

async function historyTravel(window, mode) {
  const completion = new Promise((resolve) =>
    window.addEventListener("popstate", resolve, { once: true }),
  );
  window.history[mode]();
  await completion;
}

test("Back and Forward retain explicit overlay backgrounds and each entry's scroll", async () => {
  const { window, navigate, resolveReaderNavigation, dom } = browser("/topics?offset=60");
  window.scrollY = 680;
  navigate(articleDestination("rust"));
  navigate(pageDestination("/settings/notifications"));
  await historyTravel(window, "back");
  const article = resolveReaderNavigation(
    window.location.hash.slice(1),
    window.history.state.readerNavigation,
  );
  assert.equal(article.destination.slug, "rust");
  assert.equal(article.background.href, "/topics?offset=60");
  assert.deepEqual(plain(window.history.state.readerScroll), [0, 680]);
  await historyTravel(window, "back");
  assert.equal(window.location.hash, "#/topics?offset=60");
  assert.deepEqual(plain(window.history.state.readerScroll), [0, 680]);
  await historyTravel(window, "forward");
  assert.equal(window.history.state.readerNavigation.destination.slug, "rust");
  assert.equal(window.history.state.readerNavigation.background.href, "/topics?offset=60");
  dom.window.close();
});

test("history hydration rebuilds registered page metadata and rejects foreign backgrounds", () => {
  const { resolveReaderNavigation, dom } = browser();
  const destination = articleDestination("rust");
  const saved = {
    kind: "article",
    destination,
    background: {
      kind: "page",
      href: "/settings/notifications",
      scroll: "preserve",
      route: { type: "local" },
    },
  };
  const state = resolveReaderNavigation(destination.href, saved);
  assert.equal(state.background.route.settings, "notifications");
  assert.equal(state.background.scroll, "preserve");
  for (const background of [
    { kind: "page", href: "https://outside.test/latest", scroll: "reset" },
    { kind: "page", href: "/articles/other", scroll: "reset" },
    { kind: "page", href: "javascript:alert(1)", scroll: "reset" },
  ]) {
    const invalid = resolveReaderNavigation(destination.href, { ...saved, background });
    assert.equal(invalid.background, null);
  }
  dom.window.close();
});

test("unknown saved page backgrounds retain their missing identity instead of becoming Latest", () => {
  const { resolveReaderNavigation, dom } = browser();
  const destination = articleDestination("rust");
  const href = "/topics/javascript/unknown-format?cursor=next";
  const state = resolveReaderNavigation(destination.href, {
    kind: "article",
    destination,
    background: { kind: "page", href, scroll: "preserve", route: null },
  });
  assert.equal(state.background.href, href);
  assert.equal(state.background.route, null);
  assert.equal(state.background.scroll, "preserve");
  dom.window.close();
});

test("only registered same-origin anchors become local destinations", () => {
  const { resolveExtensionLink, destinationHref, dom } = browser();
  const article = resolveExtensionLink("https://devfeed.tech/articles/%72ust?from=inbox");
  assert.equal(article.destination.slug, "rust");
  assert.equal(article.href, "#/articles/%72ust?from=inbox");
  assert.equal(
    destinationHref(pageDestination("/settings/notifications")),
    "#/settings/notifications",
  );
  for (const href of [
    "https://outside.test/articles/rust",
    "/legal/privacy",
    "/missing",
    "mailto:reader@example.com",
  ]) {
    assert.equal(resolveExtensionLink(href).destination, null);
  }
  for (const href of ["javascript:alert(1)", "data:text/html,hi", "http://["]) {
    assert.equal(resolveExtensionLink(href).href, "#");
  }
  dom.window.close();
});
