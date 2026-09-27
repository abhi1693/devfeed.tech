import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [new URL("../src/public-cache.ts", import.meta.url).pathname],
  bundle: true,
  write: false,
  format: "cjs",
});
const key = "devfeed:public-reader-cache";
const article = (slug, summary = "") => ({
  slug,
  title: slug,
  summary,
  topics: [],
  sources: [],
  tags: [],
});
const topic = (slug, description = "") => ({ id: slug, slug, description });

function cache({ raw = null, idle = false, blocked = false } = {}) {
  let now = 1_000_000_000;
  let nextId = 0;
  const pending = new Map();
  const events = {};
  const writes = [];
  const serialized = [];
  const module = { exports: {} };
  const doc = { visibilityState: "visible", addEventListener: (name, fn) => (events[name] = fn) };
  const later = (fn, delay) => {
    const id = ++nextId;
    pending.set(id, { fn, at: now + delay });
    return id;
  };
  runInNewContext(outputFiles[0].text, {
    module,
    exports: module.exports,
    Date: { now: () => now },
    JSON: {
      parse: JSON.parse,
      stringify: (value) => {
        serialized.push(value);
        return JSON.stringify(value);
      },
    },
    setTimeout: later,
    clearTimeout: (id) => pending.delete(id),
    ...(idle
      ? {
          requestIdleCallback: (fn, { timeout }) => {
            assert.equal(timeout, 1000);
            return later(fn, 20);
          },
          cancelIdleCallback: (id) => pending.delete(id),
        }
      : {}),
    sessionStorage: {
      getItem: () => raw,
      setItem: (name, json) => {
        assert.equal(name, key);
        if (blocked) throw new Error("Quota");
        raw = json;
        writes.push(json);
      },
    },
    window: { addEventListener: (name, fn) => (events[name] = fn) },
    document: doc,
  });
  return {
    ...module.exports,
    writes,
    serialized,
    saved: () => JSON.parse(raw),
    unblock: () => {
      blocked = false;
    },
    hide() {
      doc.visibilityState = "hidden";
      events.visibilitychange();
    },
    exit: () => events.pagehide(),
    advance(ms) {
      const until = now + ms;
      for (;;) {
        const next = [...pending.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > until) break;
        pending.delete(next[0]);
        now = next[1].at;
        next[1].fn();
      }
      now = until;
    },
  };
}

test("coalesces bursts and serializes only changed records after rendering can proceed", () => {
  const c = cache();
  for (let i = 0; i < 20; i++) c.rememberArticles([article(String(i))]);
  c.rememberTopics([topic("topic")]);
  assert.equal(c.writes.length, 0);
  assert.equal(c.serialized.length, 0);
  assert.equal(c.cachedArticle("0").title, "0");
  c.advance(150);
  assert.equal(c.writes.length, 1);
  assert.equal(c.serialized.length, 21);
  assert.equal(c.saved().articles.length, 20);
  assert.equal(c.saved().topics.length, 1);
});

test("skips unchanged references and fresh equivalent responses, including reordered keys", () => {
  const c = cache();
  const item = article("one");
  c.rememberArticles([item]);
  c.rememberTopics([topic("topic")]);
  c.advance(150);
  c.rememberArticles([item, { ...structuredClone(item), tags: [] }]);
  c.rememberTopics([{ description: "", slug: "topic", id: "topic" }]);
  c.rememberArticles([]);
  c.rememberTopics([]);
  c.advance(1000);
  assert.equal(c.writes.length, 1);
  assert.equal(c.serialized.length, 2);
  c.rememberArticles([{ ...item, topics: [{ slug: "nested-change" }] }]);
  c.advance(150);
  assert.equal(c.writes.length, 2);
  assert.equal(c.serialized.length, 3);
  assert.equal(c.saved().articles[0].topics[0].slug, "nested-change");
});

test("lookups change in-memory eviction order without writing storage", () => {
  const c = cache();
  c.rememberArticles(Array.from({ length: 48 }, (_, i) => article(String(i))));
  c.advance(150);
  for (let i = 0; i < 10; i++) c.cachedArticle("0");
  c.advance(1000);
  assert.equal(c.writes.length, 1);
  c.rememberArticles([article("new")]);
  c.advance(150);
  assert.ok(c.cachedArticle("0"));
  assert.equal(c.cachedArticle("1"), undefined);
  assert.equal(c.serialized.length, 49);
});

test("bounds record counts before serializing discarded entries", () => {
  const c = cache();
  c.rememberArticles(Array.from({ length: 60 }, (_, i) => article(String(i))));
  c.rememberTopics(Array.from({ length: 605 }, (_, i) => topic(String(i))));
  c.advance(150);
  assert.equal(c.saved().articles.length, 48);
  assert.equal(c.saved().topics.length, 600);
  assert.equal(c.serialized.length, 648);
});

test("evicts by tracked serialized sizes without repeatedly serializing the cache", () => {
  const c = cache();
  c.rememberArticles(Array.from({ length: 4 }, (_, i) => article(String(i), "界".repeat(800_000))));
  c.rememberTopics([topic("large", "x".repeat(800_000))]);
  c.advance(150);
  assert.ok(c.writes[0].length <= 3_000_000);
  assert.equal(c.serialized.length, 5);
  assert.equal(c.saved().topics.length, 0);
  assert.deepEqual(
    c.saved().articles.map(({ slug }) => slug),
    ["1", "2", "3"],
  );
  c.rememberArticles([article("1", "small")]);
  c.rememberTopics([topic("fits", "x".repeat(800_000))]);
  c.advance(150);
  assert.equal(c.saved().articles.length, 3);
  assert.equal(c.saved().topics.length, 1);
  assert.equal(c.serialized.length, 7);
  c.rememberArticles([article("oversized", "x".repeat(3_000_001))]);
  c.advance(150);
  assert.ok(c.writes.at(-1).length <= 3_000_000);
  assert.equal(c.cachedArticle("oversized"), undefined);
  assert.equal(c.saved().articles.length, 3);
  assert.equal(c.saved().topics.length, 1);
});

test("restores valid storage and preserves expiry without writes for unchanged data", () => {
  const raw = JSON.stringify({
    at: 1_000_000_000 - 1000,
    articles: [article("saved")],
    topics: [topic("saved")],
  });
  const c = cache({ raw });
  assert.equal(c.cachedArticle("saved").title, "saved");
  assert.equal(c.cachedTopic("saved").id, "saved");
  c.rememberArticles([article("saved")]);
  c.advance(1000);
  assert.equal(c.writes.length, 0);
  assert.equal(c.saved().at, 1_000_000_000 - 1000);
  for (const raw of [
    "null",
    "bad JSON",
    JSON.stringify({ at: 0, articles: [article("saved")] }),
    JSON.stringify({ at: 2_000_000_000, articles: [article("saved")] }),
  ])
    assert.equal(cache({ raw }).cachedArticle("saved"), undefined);
});

test("flushes pending work on hide or page exit and cancels duplicate scheduled writes", () => {
  const c = cache({ idle: true });
  c.rememberArticles([article("one")]);
  c.advance(150);
  assert.equal(c.writes.length, 0);
  c.hide();
  assert.equal(c.writes.length, 1);
  c.advance(2000);
  c.exit();
  assert.equal(c.writes.length, 1);
  c.rememberTopics([topic("two")]);
  c.exit();
  c.advance(2000);
  assert.equal(c.writes.length, 2);
});

test("uses idle time and keeps a bounded batch delay under continuous updates", () => {
  const c = cache({ idle: true });
  c.rememberArticles([article("one")]);
  c.advance(100);
  c.rememberArticles([article("two")]);
  c.advance(50);
  assert.equal(c.writes.length, 0);
  c.advance(20);
  assert.equal(c.writes.length, 1);
  assert.equal(c.saved().articles.length, 2);
});

test("keeps previews usable when storage fails and retries without reserialization", () => {
  const c = cache({ blocked: true });
  c.rememberArticles([article("one")]);
  c.advance(150);
  assert.equal(c.cachedArticle("one").title, "one");
  assert.equal(c.writes.length, 0);
  c.unblock();
  c.exit();
  assert.equal(c.writes.length, 1);
  assert.equal(c.serialized.length, 1);
});
