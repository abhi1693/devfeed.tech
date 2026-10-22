import assert from "node:assert/strict";
import { columnsCatalog } from "./columns-workspace.mjs";
import { mockManagedImages, withManagedImage } from "./managed-images.mjs";
import { createFixtureFeedViews, installFixtureFeedViews } from "./feed-view-fixture.mjs";

export const columnsSharedArticle = {
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  slug: "columns-shared-story",
  canonical_url: "https://publisher.test/columns-shared-story",
  title: "Shared Columns article",
  summary: "One article can belong to several independent reading feeds.",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  image_variants: [],
  author: null,
  content_type: "article",
  content_format: "article",
  language: "en",
  published_at: "2026-10-21T00:00:00Z",
  feed_at: "2026-10-21T00:00:00Z",
  tags: [],
  topics: [],
  sources: [columnsCatalog.sources[0]],
};

export const columnsFeedSelections = [
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", feed: { kind: "latest" } },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    feed: { kind: "source", id: columnsCatalog.sources[0].id },
  },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
    feed: { kind: "topic", id: columnsCatalog.topics[0].id },
  },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4",
    feed: { kind: "tag", id: columnsCatalog.tags[0].id },
  },
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", feed: { kind: "recommended" } },
];

// Keep stress selections separate from the small catalog used by starter recipes.
const stressCatalog = {
  sources: [
    ...columnsCatalog.sources,
    ...Array.from({ length: 2 }, (_, index) => ({
      ...columnsCatalog.sources[0],
      id: `77777777-7777-4777-8777-${String(index + 1).padStart(12, "0")}`,
      name: `Bounded Source ${index + 1}`,
      slug: `bounded-source-${index + 1}`,
    })),
  ],
  topics: [
    ...columnsCatalog.topics,
    ...Array.from({ length: 2 }, (_, index) => ({
      ...columnsCatalog.topics[0],
      id: `88888888-8888-4888-8888-${String(index + 1).padStart(12, "0")}`,
      name: `Bounded Topic ${index + 1}`,
      slug: `bounded-topic-${index + 1}`,
    })),
  ],
  tags: [
    ...columnsCatalog.tags,
    ...Array.from({ length: 3 }, (_, index) => ({
      id: `99999999-9999-4999-8999-${String(index + 1).padStart(12, "0")}`,
      name: `Bounded Tag ${index + 1}`,
      slug: `bounded-tag-${index + 1}`,
    })),
  ],
};
export const columnsStressSelections = [
  { kind: "latest" },
  ...Object.entries(stressCatalog).flatMap(([kind, items]) =>
    items.map(({ id }) => ({ kind: kind.slice(0, -1), id })),
  ),
].map((feed, index) => ({
  id: `dddddddd-dddd-4ddd-8ddd-${String(index).padStart(12, "0")}`,
  feed,
}));

const deferred = () => {
  let resolve;
  const promise = new Promise((value) => {
    resolve = value;
  });
  return { promise, resolve };
};

function identify(url, stress = false) {
  if (url.pathname.endsWith("/user/feed")) return "recommended";
  if (stress) {
    const index = columnsStressSelections.slice(1).findIndex(({ feed }) => {
      const item = stressCatalog[`${feed.kind}s`].find(({ id }) => id === feed.id);
      const key = feed.kind === "source" ? "source_id" : feed.kind;
      const value = feed.kind === "tag" ? item.slug : item.id;
      return url.searchParams.get(key) === value;
    });
    if (index >= 0) return `stress${index + 1}`;
  }
  if (url.searchParams.has("source_id")) return "source";
  if (url.searchParams.has("topic")) {
    const topic = url.searchParams.get("topic");
    return [columnsCatalog.topics[1].id, columnsCatalog.topics[1].slug].includes(topic)
      ? "rust"
      : "topic";
  }
  if (url.searchParams.has("tag")) return "tag";
  return "latest";
}

function article(kind, page, index) {
  if (index < 2) return structuredClone(columnsSharedArticle);
  const namespace = kind.startsWith("stress")
    ? 6 + Number(kind.slice(6))
    : ["latest", "source", "topic", "tag", "recommended", "rust"].indexOf(kind) + 1;
  const value = {
    ...columnsSharedArticle,
    id: `bbbbbbbb-${String(namespace).padStart(4, "0")}-4bbb-8bbb-${String(page * 1000 + index).padStart(12, "0")}`,
    slug: `columns-${kind}-${page}-${index}`,
    title: `${kind === "recommended" ? "Recommended" : kind[0].toUpperCase() + kind.slice(1)} article ${page + 1}.${index}`,
  };
  if (page === 0 && index === 3) withManagedImage(value);
  return value;
}

export function columnsFixtureArticle(slug) {
  if (slug === columnsSharedArticle.slug) return structuredClone(columnsSharedArticle);
  const match = /^columns-(latest|source|topic|tag|recommended|rust|stress\d+)-(\d+)-(\d+)$/.exec(
    slug,
  );
  return match ? article(match[1], Number(match[2]), Number(match[3])) : null;
}

/** Browser-local API state supports delayed responses without replacing real reader components. */
export async function installColumnsFeedFixture(page, { signedIn = false } = {}) {
  const context = page.context();
  await mockManagedImages(context);
  const requests = [];
  const metadata = [];
  const writes = [];
  const mutations = [];
  const engagementReads = [];
  const empty = new Set();
  const values = new Map();
  const unavailable = new Set();
  const failures = [];
  const holds = [];
  let activeMember = signedIn;
  let pause = null;
  let active = 0;
  let maximum = 0;
  let stress = false;
  let visualArticles = null;
  let ordinaryArticles = null;
  let recommendation = { has_interests: true, status: "ready" };
  let settings = {
    view: "columns",
    columns: structuredClone(columnsFeedSelections),
    content_types: ["article", "news"],
    languages: ["en"],
  };
  const scopedViews = await installFixtureFeedViews(page, {
    store: createFixtureFeedViews(() => settings.view),
    isSignedIn: () => activeMember,
  });
  const engagement = (id) => {
    if (!values.has(id))
      values.set(id, { article_id: id, liked: false, likes: 0, opens: 0, bookmarked: false });
    return values.get(id);
  };
  const settingsPattern = /\/api\/v1\/user\/settings\/feed(?:\?|$)/;
  const settingsHandler = async (route) => {
    if (!signedIn) return route.fallback();
    if (route.request().method() === "PUT") {
      settings = { ...settings, ...structuredClone(route.request().postDataJSON()) };
      writes.push(settings);
    }
    return route.fulfill({ json: settings });
  };
  const columnsSettingsPattern = /\/api\/v1\/user\/settings\/columns(?:\?|$)/;
  const columnsSettingsHandler = async (route) => {
    if (!signedIn) return route.fallback();
    const scope = new URL(route.request().url()).searchParams.get("workspace") ?? "latest";
    if (scope !== "my_feed") return route.fulfill({ json: { initialized: true, columns: [] } });
    if (route.request().method() === "PUT") {
      const candidate = route.request().postDataJSON();
      if (candidate.mode === "replace" || !settings.columns.length) {
        settings = { ...settings, columns: structuredClone(candidate.columns) };
        writes.push(settings);
      }
    }
    return route.fulfill({ json: { initialized: true, columns: settings.columns } });
  };
  const catalogPattern = /\/api\/v1\/(topics|sources|tags)(?:\/[^/?]+)?(?:\?|$)/;
  const catalogHandler = async (route) => {
    const url = new URL(route.request().url());
    const [, kind, key] = url.pathname.match(/\/api\/v1\/(topics|sources|tags)(?:\/([^/]+))?$/);
    const available = (stress ? stressCatalog : columnsCatalog)[kind].filter(
      ({ id }) => !unavailable.has(id),
    );
    const request = { kind, key, url: url.href, started: Date.now(), active: ++active };
    maximum = Math.max(maximum, active);
    metadata.push(request);
    try {
      if (pause) await pause.promise;
      if (key) {
        const item = available.find(
          ({ id, slug }) => id === key || slug === decodeURIComponent(key),
        );
        return await route.fulfill({
          status: item ? 200 : 404,
          json: item ?? { detail: "Not found" },
        });
      }
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      return await route.fulfill({
        json: {
          items: available.filter(({ name }) => name.toLowerCase().includes(query)),
          next_cursor: null,
        },
      });
    } catch (error) {
      request.deliveryError = String(error.message);
    } finally {
      request.finished = Date.now();
      active -= 1;
    }
  };
  const feedPattern = /\/api\/v1\/(?:user\/)?feed(?:\?|$)/;
  const feedHandler = async (route) => {
    const url = new URL(route.request().url());
    // The bundled reader loads its surrounding feed before mounting the workspace.
    // Keep that bootstrap on the existing fixture; Columns always supplies a page limit.
    const personalShell =
      url.pathname.endsWith("/user/feed") &&
      (url.search.endsWith("&") || url.searchParams.has("generation"));
    if (!url.searchParams.has("limit") || personalShell)
      return route.fulfill({
        json: {
          items: ordinaryArticles ? structuredClone(ordinaryArticles) : [article("latest", 0, 3)],
          next_cursor: null,
          ...(url.pathname.endsWith("/user/feed")
            ? { has_interests: true, status: "ready", generation: "ordinary-feed", reasons: {} }
            : {}),
        },
      });
    const kind = identify(url, stress);
    const cursor = url.searchParams.get("cursor") ?? "";
    const page = cursor ? Number(cursor.split(":").at(-1)) : 0;
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const request = {
      kind,
      cursor,
      page,
      limit,
      url: url.href,
      method: route.request().method(),
      started: Date.now(),
      active: ++active,
    };
    maximum = Math.max(maximum, active);
    requests.push(request);
    const pending = holds.find(
      (hold) =>
        !hold.used && hold.kind === kind && (hold.cursor === undefined || hold.cursor === cursor),
    );
    if (pending) {
      pending.used = true;
      pending.started.resolve(request);
    }
    try {
      if (pause) await pause.promise;
      if (pending) await pending.released.promise;
      const failure = failures.find(
        (failure) =>
          failure.kind === kind && (failure.cursor === undefined || failure.cursor === cursor),
      );
      if (failure) {
        failures.splice(failures.indexOf(failure), 1);
        request.status = failure.status;
        return await route.fulfill({
          status: failure.status,
          json: { detail: "This column temporarily failed" },
        });
      }
      const eligible =
        !empty.has(kind) &&
        (kind !== "recommended" ||
          (recommendation.has_interests && recommendation.status === "ready"));
      const size = stress ? limit : 5;
      const body = {
        items: eligible
          ? Array.from({ length: size }, (_, index) =>
              article(kind, page, stress ? index + 2 : index),
            )
          : [],
        next_cursor: eligible && page < 12 ? `${kind}:${page + 1}` : null,
        ...(kind === "recommended"
          ? { ...recommendation, generation: "columns-fixture-generation", reasons: {} }
          : {}),
      };
      if (visualArticles) {
        body.items = structuredClone(visualArticles);
        body.next_cursor = null;
      }
      request.status = 200;
      return await route.fulfill({ json: body });
    } catch (error) {
      request.deliveryError = String(error.message);
    } finally {
      request.finished = Date.now();
      active -= 1;
    }
  };
  const engagementPattern = /\/api\/v1\/user\/engagement(?:\?|$)/;
  const engagementHandler = (route) => {
    const ids = new URL(route.request().url()).searchParams.getAll("article_id");
    engagementReads.push(ids);
    return route.fulfill({ json: ids.map((id) => engagement(id)) });
  };
  const mutationPattern = /\/api\/v1\/user\/articles\/[^/]+\/(?:like|bookmark|open)$/;
  const articlePattern = /\/api\/v1\/articles\/columns-[^/?]+(?:\?|$)/;
  const articleHandler = (route) => {
    const value = columnsFixtureArticle(new URL(route.request().url()).pathname.split("/").at(-1));
    return route.fulfill({
      status: value ? 200 : 404,
      json: value ? { article: value, topic: null } : { detail: "Not found" },
    });
  };
  const mutationHandler = async (route) => {
    const request = route.request();
    const [, id, action] = new URL(request.url()).pathname.match(
      /\/articles\/([^/]+)\/(like|bookmark|open)$/,
    );
    const value = engagement(id);
    if (action !== "open") {
      assert.equal(signedIn, true, "Only authenticated readers mutate likes and bookmarks");
      assert.ok((await request.allHeaders())["x-csrf-token"]);
    }
    if (action === "like") {
      value.liked = request.postDataJSON().liked;
      value.likes = value.liked ? 1 : 0;
    }
    if (action === "bookmark") value.bookmarked = request.postDataJSON().bookmarked;
    if (action === "open") value.opens += 1;
    mutations.push({ id, action, ...value });
    return route.fulfill({
      json: action === "bookmark" ? { article_id: id, bookmarked: value.bookmarked } : value,
    });
  };
  const handlers = [
    [settingsPattern, settingsHandler],
    [columnsSettingsPattern, columnsSettingsHandler],
    [catalogPattern, catalogHandler],
    [feedPattern, feedHandler],
    [engagementPattern, engagementHandler],
    [mutationPattern, mutationHandler],
    [articlePattern, articleHandler],
  ];
  for (const [pattern, handler] of handlers) await context.route(pattern, handler);
  return {
    requests,
    metadata,
    writes,
    mutations,
    engagementReads,
    scopedViews,
    unavailable,
    get settings() {
      return settings;
    },
    setColumns(columns) {
      settings = { ...settings, columns: structuredClone(columns) };
    },
    setMember(value) {
      activeMember = value;
    },
    setSettings(value) {
      settings = { ...settings, ...structuredClone(value) };
      if (Object.hasOwn(value, "view")) scopedViews.store.reset();
    },
    setStress(value) {
      stress = value;
    },
    setVisualArticles(value) {
      visualArticles = structuredClone(value);
    },
    setOrdinaryArticles(value) {
      ordinaryArticles = structuredClone(value);
    },
    setEmpty(kind, value) {
      if (value) empty.add(kind);
      else empty.delete(kind);
    },
    setRecommendation(value) {
      recommendation = { ...recommendation, ...value };
    },
    get maximum() {
      return maximum;
    },
    get active() {
      return active;
    },
    resetMaximum() {
      maximum = active;
    },
    failNext(kind, { cursor, status = 503 } = {}) {
      failures.push({ kind, cursor, status });
    },
    holdNext(kind, { cursor } = {}) {
      const hold = { kind, cursor, used: false, started: deferred(), released: deferred() };
      holds.push(hold);
      return { started: hold.started.promise, release: () => hold.released.resolve() };
    },
    pause() {
      pause = deferred();
    },
    resume() {
      pause?.resolve();
      pause = null;
    },
    async cleanup() {
      pause?.resolve();
      for (const hold of holds) hold.released.resolve();
      for (const [pattern, handler] of handlers) await context.unroute(pattern, handler);
      await scopedViews.cleanup();
    },
  };
}
