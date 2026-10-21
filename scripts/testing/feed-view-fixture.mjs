import assert from "node:assert/strict";

const layouts = new Set(["cards", "compact", "stacked", "columns"]);
const fixed = new Set(["my_feed", "latest", "trending", "read_later", "search"]);
const content = new Set(["article", "news", "tutorial", "release", "comparison", "opinion"]);
const uuid = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;

export function canonicalFixtureFeed(value) {
  if (fixed.has(value)) return value;
  const [kind, id, extra] = String(value ?? "").split(":");
  if (extra !== undefined) return null;
  if (kind === "content" && content.has(id)) return value;
  if (["topic", "source", "tag"].includes(kind) && uuid.test(id))
    return `${kind}:${id.toLowerCase()}`;
  return null;
}

/** Match the API's immutable legacy fallback and independent scoped overrides. */
export function createFixtureFeedViews(legacyView = () => "cards") {
  let baseline = null;
  const views = new Map();
  return {
    views,
    read(feed) {
      const key = canonicalFixtureFeed(feed);
      if (!key) return null;
      const view = views.get(key) ?? baseline ?? legacyView();
      return {
        view:
          view === "columns" && key !== "my_feed" ? "cards" : layouts.has(view) ? view : "cards",
      };
    },
    write(feed, payload) {
      const key = canonicalFixtureFeed(feed);
      if (
        !key ||
        !payload ||
        Object.keys(payload).length !== 1 ||
        !layouts.has(payload.view) ||
        (payload.view === "columns" && key !== "my_feed")
      )
        return null;
      if (!views.has(key) && views.size >= 256) return null;
      baseline ??= legacyView();
      views.set(key, payload.view);
      return this.read(key);
    },
    reset() {
      baseline = null;
      views.clear();
    },
  };
}

export async function installFixtureFeedViews(
  page,
  { store = createFixtureFeedViews(), isSignedIn = () => true, onSave = () => {} } = {},
) {
  const reads = [];
  const writes = [];
  const failures = [];
  const pattern = /\/api\/v1\/user\/settings\/feed-view(?:\?|$)/;
  const handler = async (route) => {
    const request = route.request();
    if (!isSignedIn()) return route.fulfill({ status: 401, json: {} });
    const feed = new URL(request.url()).searchParams.get("feed");
    const currentStore = typeof store === "function" ? store() : store;
    if (request.method() === "PUT") {
      assert.ok(
        (await request.allHeaders())["x-csrf-token"],
        "Scoped layout writes retain CSRF protection",
      );
      const payload = request.postDataJSON();
      const record = { feed, payload: structuredClone(payload) };
      writes.push(record);
      const failed = failures.findIndex((key) => key === feed);
      if (failed >= 0) {
        failures.splice(failed, 1);
        return route.fulfill({ status: 503, json: { detail: "Layout temporarily unavailable" } });
      }
      const value = currentStore.write(feed, payload);
      if (value) onSave(record);
      return route.fulfill({
        status: value ? 200 : 422,
        json: value ?? { detail: "Invalid feed layout" },
      });
    }
    assert.equal(request.method(), "GET");
    reads.push(feed);
    const value = currentStore.read(feed);
    return route.fulfill({
      status: value ? 200 : 422,
      json: value ?? { detail: "Invalid feed scope" },
    });
  };
  await page.context().route(pattern, handler);
  return {
    store,
    reads,
    writes,
    failNextSave(feed) {
      failures.push(feed);
    },
    cleanup: () => page.context().unroute(pattern, handler),
  };
}
