import assert from "node:assert/strict";
import { installColumnsFeedFixture } from "./columns-feed-fixture.mjs";
import { columnsCatalog } from "./columns-workspace.mjs";

export const personalStarterFeeds = [
  { kind: "topic", id: columnsCatalog.topics[0].id },
  { kind: "source", id: columnsCatalog.sources[0].id },
  { kind: "tag", id: columnsCatalog.tags[0].id },
  { kind: "topic", id: columnsCatalog.topics[1].id },
];
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
};

/** Match the account API's legacy fallback and conditional, independently scoped writes. */
export async function installPersonalColumnsFixture(page, { signedIn = false } = {}) {
  const fixture = await installColumnsFeedFixture(page, { signedIn });
  const context = page.context();
  let latest = { initialized: false, columns: [] };
  let personal = null;
  let feeds = structuredClone(personalStarterFeeds);
  let activeMember = signedIn;
  const requests = [];
  const writes = [];
  const holds = [];
  const saveHolds = [];
  const privateRequests = [];
  const privateRequest = (request) => {
    if (
      /\/api\/v1\/user\/(?:settings\/(?:columns(?:\/starters)?|feed(?:-view)?)|feed)(?:\?|$)/.test(
        request.url(),
      )
    )
      privateRequests.push({ url: request.url(), method: request.method(), member: activeMember });
  };
  page.on("request", privateRequest);
  const effectivePersonal = () => personal ?? latest;
  const setLatest = (value) => {
    // The first Latest mutation preserves the previous effective legacy My feed workspace.
    if (personal === null) personal = structuredClone(latest);
    latest = structuredClone(value);
    fixture.setColumns(latest.columns);
  };
  const statePattern = /\/api\/v1\/user\/settings\/columns(?:\?|$)/;
  const stateHandler = async (route) => {
    if (!activeMember) return route.fulfill({ status: 401, json: {} });
    const request = route.request();
    const scope = new URL(request.url()).searchParams.get("workspace") ?? "latest";
    assert.ok(["latest", "my_feed"].includes(scope));
    if (request.method() === "PUT") {
      assert.ok((await request.allHeaders())["x-csrf-token"]);
      const candidate = request.postDataJSON();
      assert.ok(["replace", "initialize", "restore_empty"].includes(candidate.mode));
      const hold = saveHolds.find((value) => !value.used);
      if (hold) {
        hold.used = true;
        hold.started.resolve(request);
        await hold.released.promise;
      }
      const current = scope === "latest" ? latest : effectivePersonal();
      const previous = structuredClone(current);
      if (
        candidate.mode === "replace" ||
        (!current.columns.length && (candidate.mode === "restore_empty" || !current.initialized))
      ) {
        const value = { initialized: true, columns: structuredClone(candidate.columns) };
        if (scope === "latest") setLatest(value);
        else personal = value;
      }
      writes.push({ scope, ...structuredClone(candidate), previous });
    }
    const result = scope === "latest" ? latest : effectivePersonal();
    requests.push({ scope, method: request.method() });
    await route.fulfill({ json: result });
  };
  const startersPattern = /\/api\/v1\/user\/settings\/columns\/starters(?:\?|$)/;
  const startersHandler = async (route) => {
    if (!activeMember) return route.fulfill({ status: 401, json: {} });
    assert.equal(route.request().method(), "GET");
    const record = { kind: "personal-starters", started: Date.now() };
    requests.push(record);
    const hold = holds.find((value) => !value.used);
    if (hold) {
      hold.used = true;
      hold.record = record;
      hold.started.resolve();
      await hold.released.promise;
    }
    try {
      await route.fulfill({ json: { feeds } });
    } catch (error) {
      record.deliveryError = error.message;
    } finally {
      record.finished = Date.now();
    }
  };
  const settingsPattern = /\/api\/v1\/user\/settings\/feed(?:\?|$)/;
  const settingsHandler = async (route) => {
    if (!activeMember) return route.fallback();
    if (route.request().method() === "PUT") {
      const candidate = route.request().postDataJSON();
      if (Object.hasOwn(candidate, "columns"))
        setLatest({ initialized: true, columns: candidate.columns });
    }
    await route.fallback();
  };
  for (const [pattern, handler] of [
    [statePattern, stateHandler],
    [startersPattern, startersHandler],
    [settingsPattern, settingsHandler],
  ])
    await context.route(pattern, handler);
  fixture.setSettings({ view: "columns", columns: [] });
  return {
    fixture,
    requests,
    writes,
    privateRequests,
    get latest() {
      return structuredClone(latest);
    },
    get personal() {
      return structuredClone(effectivePersonal());
    },
    setPersonal(value) {
      personal = value === null ? null : structuredClone(value);
    },
    setLegacy(value) {
      latest = structuredClone(value);
      personal = null;
      fixture.setColumns(latest.columns);
    },
    setFeeds(value) {
      feeds = structuredClone(value);
    },
    setMember(value) {
      activeMember = value;
      fixture.setMember(value);
    },
    holdStarters() {
      const value = { used: false, started: deferred(), released: deferred() };
      holds.push(value);
      return {
        started: value.started.promise,
        release: () => value.released.resolve(),
        record: () => value.record,
      };
    },
    holdSave() {
      const value = { used: false, started: deferred(), released: deferred() };
      saveHolds.push(value);
      return {
        started: value.started.promise,
        release: () => value.released.resolve(),
      };
    },
    async cleanup() {
      for (const value of holds) value.released.resolve();
      for (const value of saveHolds) value.released.resolve();
      page.off("request", privateRequest);
      for (const [pattern, handler] of [
        [statePattern, stateHandler],
        [startersPattern, startersHandler],
        [settingsPattern, settingsHandler],
      ])
        await context.unroute(pattern, handler);
      await fixture.cleanup();
    },
  };
}
