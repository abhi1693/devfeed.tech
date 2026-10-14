// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { analyticsEventName, type AnalyticsEvent } from "@/lib/analytics";
import { userRequest } from "@/lib/user";
import {
  setArticleLike,
  setArticleBookmark,
  setTopicFollow,
  setSourceFollow,
  saveTopicPreferences,
  saveSourcePreferences,
  saveFeedSettings,
  saveAppearanceSettings,
  suggestSource,
  previewSourceSuggestion,
} from "@/lib/user-mutations";

const id = "ABCDEF00-0000-4000-8000-000000000001";
const options = { csrfToken: "private-csrf-token" };
const feed = { view: "compact", content_types: ["article", "news"], languages: ["en"] } as const;
const appearance = {
  theme: "system",
  timezone: "Asia/Kolkata",
  date_format: "iso",
  time_format: "24",
} as const;
const suggestion = {
  source_type: "publisher",
  feed_url: "https://example.com/private?token=secret",
  name: "Personal name",
} as const;
const receipt = {
  id,
  name: "Personal name",
  approval_status: "pending",
  created_at: "2026-10-15T12:00:00Z",
};

type MutationCase = {
  name: string;
  invoke: (signal?: AbortSignal) => Promise<unknown>;
  response: unknown;
  event: AnalyticsEvent;
  invalidResponse: unknown;
  interests: boolean;
};
const cases: MutationCase[] = [
  {
    name: "like",
    invoke: (signal) => setArticleLike(id, true, { ...options, signal }),
    response: { article_id: id, liked: true, likes: 3, opens: 5, bookmarked: false },
    event: { name: "article_like", params: { article_id: id } },
    invalidResponse: { article_id: id, liked: "true", likes: 3, opens: 5 },
    interests: false,
  },
  {
    name: "unlike",
    invoke: (signal) => setArticleLike(id, false, { ...options, signal }),
    response: { article_id: id, liked: false, likes: 2, opens: 5, bookmarked: false },
    event: { name: "article_unlike", params: { article_id: id } },
    invalidResponse: { article_id: id, liked: false, likes: "2", opens: 5 },
    interests: false,
  },
  {
    name: "bookmark",
    invoke: (signal) => setArticleBookmark(id, true, { ...options, signal }),
    response: { article_id: id, bookmarked: true },
    event: { name: "article_bookmark", params: { article_id: id } },
    invalidResponse: { article_id: id, bookmarked: "true" },
    interests: false,
  },
  {
    name: "unbookmark",
    invoke: (signal) => setArticleBookmark(id, false, { ...options, signal }),
    response: { article_id: id, bookmarked: false },
    event: { name: "article_unbookmark", params: { article_id: id } },
    invalidResponse: { article_id: id },
    interests: false,
  },
  {
    name: "topic follow",
    invoke: (signal) => setTopicFollow(id, true, { ...options, signal }),
    response: { followed: true },
    event: { name: "topic_follow", params: { topic_id: id } },
    invalidResponse: { followed: "true" },
    interests: true,
  },
  {
    name: "topic unfollow",
    invoke: (signal) => setTopicFollow(id, false, { ...options, signal }),
    response: { followed: false },
    event: { name: "topic_unfollow", params: { topic_id: id } },
    invalidResponse: {},
    interests: true,
  },
  {
    name: "source follow",
    invoke: (signal) => setSourceFollow(id, true, { ...options, signal }),
    response: { followed: true },
    event: { name: "source_follow", params: { source_id: id } },
    invalidResponse: { followed: null },
    interests: true,
  },
  {
    name: "source unfollow",
    invoke: (signal) => setSourceFollow(id, false, { ...options, signal }),
    response: { followed: false },
    event: { name: "source_unfollow", params: { source_id: id } },
    invalidResponse: { followed: 0 },
    interests: true,
  },
  {
    name: "topics saved",
    invoke: (signal) => saveTopicPreferences([id], { ...options, signal }),
    response: { topic_ids: [id] },
    event: { name: "topics_saved", params: { selected_count: 1 } },
    invalidResponse: { topic_ids: [null] },
    interests: true,
  },
  {
    name: "sources saved",
    invoke: (signal) => saveSourcePreferences([], { ...options, signal }),
    response: { source_ids: [] },
    event: { name: "sources_saved", params: { selected_count: 0 } },
    invalidResponse: { source_ids: "" },
    interests: true,
  },
  {
    name: "feed settings",
    invoke: (signal) =>
      saveFeedSettings(
        { ...feed, content_types: [...feed.content_types], languages: [...feed.languages] },
        { ...options, signal },
      ),
    response: feed,
    event: { name: "feed_settings_saved", params: { feed_view: "compact", selected_count: 2 } },
    invalidResponse: { ...feed, languages: [null] },
    interests: true,
  },
  {
    name: "appearance settings",
    invoke: (signal) => saveAppearanceSettings(appearance, { ...options, signal }),
    response: appearance,
    event: { name: "appearance_settings_saved", params: { theme: "system" } },
    invalidResponse: { ...appearance, theme: "unknown" },
    interests: false,
  },
  {
    name: "source suggestion",
    invoke: (signal) => suggestSource(suggestion, { ...options, signal }),
    response: receipt,
    event: { name: "source_suggested", params: { source_type: "publisher" } },
    invalidResponse: { ...receipt, id: null },
    interests: false,
  },
];

let dispatched: Event[];
beforeEach(() => {
  dispatched = [];
  vi.spyOn(window, "dispatchEvent").mockImplementation((event) => {
    dispatched.push(event);
    return true;
  });
});
afterEach(() => vi.unstubAllGlobals());
const analytics = () =>
  dispatched
    .filter((event) => event.type === analyticsEventName)
    .map((event) => (event as CustomEvent<AnalyticsEvent>).detail);
const interests = () => dispatched.filter((event) => event.type === "devfeed:interests-changed");

it.each(cases)("declares successful $name effects after a valid acknowledgement", async (test) => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(test.response));
  vi.stubGlobal("fetch", fetcher);
  expect(await test.invoke()).toEqual(test.response);
  expect(analytics()).toEqual([test.event]);
  expect(interests()).toHaveLength(test.interests ? 1 : 0);
  const init = fetcher.mock.calls[0][1] as RequestInit;
  expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(options.csrfToken);
  expect(init.credentials).toBe("same-origin");
  expect(init.cache).toBe("no-store");
});

it.each(cases)("suppresses $name effects after rejection", async (test) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ detail: "Failed" }, { status: 503 })),
  );
  await expect(test.invoke()).rejects.toThrow();
  expect(dispatched).toEqual([]);
});

it.each(cases)("suppresses $name effects after an invalid acknowledgement", async (test) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(test.invalidResponse)));
  await expect(test.invoke()).rejects.toThrow();
  expect(dispatched).toEqual([]);
});

it.each(cases)("suppresses $name effects when JSON parsing fails", async (test) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{malformed")));
  await expect(test.invoke()).rejects.toThrow();
  expect(dispatched).toEqual([]);
});

it.each(cases)("suppresses $name effects when cancelled during acknowledgement", async (test) => {
  const controller = new AbortController();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        controller.abort();
        return test.response;
      },
    }),
  );
  await expect(test.invoke(controller.signal)).rejects.toThrow();
  expect(dispatched).toEqual([]);
});

it.each([id, "future:opaque/id with spaces"])(
  "retains caller identifiers without URL parsing: %s",
  async (resourceId) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ followed: true }));
    vi.stubGlobal("fetch", fetcher);
    await setTopicFollow(resourceId, true, options);
    expect(analytics()).toEqual([{ name: "topic_follow", params: { topic_id: resourceId } }]);
    expect(fetcher.mock.calls[0][0]).toBe(
      `/api/v1/user/preferences/topics/${encodeURIComponent(resourceId)}`,
    );
  },
);

it("uses the caller's article identity rather than extracting it from the transport URL", async () => {
  const resourceId = "future:article/id with spaces";
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ article_id: resourceId, liked: true, likes: 1, opens: 2, bookmarked: false }),
    );
  vi.stubGlobal("fetch", fetcher);
  await setArticleLike(resourceId, true, options);
  expect(analytics()).toEqual([{ name: "article_like", params: { article_id: resourceId } }]);
  expect(fetcher.mock.calls[0][0]).toBe(
    `/api/v1/user/articles/${encodeURIComponent(resourceId)}/like`,
  );
});

it("does not publish effects from an already aborted request", async () => {
  const controller = new AbortController();
  controller.abort();
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(
    setTopicFollow(id, true, { ...options, signal: controller.signal }),
  ).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
  expect(dispatched).toEqual([]);
});

it("waits for JSON acknowledgement before invalidating interests or emitting analytics", async () => {
  let acknowledge!: (value: unknown) => void;
  const json = vi.fn(() => new Promise((resolve) => (acknowledge = resolve)));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json }));
  const pending = setTopicFollow(id, true, options);
  await vi.waitFor(() => expect(json).toHaveBeenCalledOnce());
  expect(dispatched).toEqual([]);
  acknowledge({ followed: true });
  await pending;
  expect(analytics()).toEqual([{ name: "topic_follow", params: { topic_id: id } }]);
  expect(interests()).toHaveLength(1);
});

it("keeps the caller's identifier when a backend normalizes a UUID acknowledgement", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ article_id: id.toLowerCase(), bookmarked: true })),
  );
  await setArticleBookmark(id, true, options);
  expect(analytics()).toEqual([{ name: "article_bookmark", params: { article_id: id } }]);
});

it("preserves session expiry while publishing no mutation success", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({}, { status: 401 })));
  await expect(setTopicFollow(id, true, options)).rejects.toThrow();
  expect(dispatched.map((event) => event.type)).toEqual(["devfeed:user-session-expired"]);
});

it("keeps source URLs, personal names, dates, timezones and raw request bodies out of events", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json(receipt))
      .mockResolvedValueOnce(Response.json(appearance)),
  );
  await suggestSource(suggestion, options);
  await saveAppearanceSettings(appearance, options);
  expect(analytics()).toEqual([
    { name: "source_suggested", params: { source_type: "publisher" } },
    { name: "appearance_settings_saved", params: { theme: "system" } },
  ]);
  const serialized = JSON.stringify(analytics());
  for (const privateValue of [
    suggestion.feed_url,
    suggestion.name,
    receipt.created_at,
    appearance.timezone,
    options.csrfToken,
  ])
    expect(serialized).not.toContain(privateValue);
});

const quietRequests: [string, string, unknown][] = [
  ["preferences", "GET", { topic_ids: [id] }],
  ["preferences", "PUT", { topic_ids: [id] }],
  ["preferences/future-operation", "PUT", { topic_ids: [id] }],
  ["preferences/sources", "PUT", { source_ids: [id] }],
  ["settings/feed", "PUT", feed],
  ["settings/profile", "PUT", { name: "Personal name", email: "person@example.com" }],
  ["engagement", "POST", []],
  [`articles/${id}/open`, "POST", { opens: 1 }],
  ["sources/suggestions/preview", "POST", { name: "Feed" }],
];
it.each(quietRequests)(
  "keeps low-level %s %s free of semantic side effects",
  async (path, method, response) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(response)));
    expect(await userRequest(path, { method })).toEqual(response);
    expect(dispatched).toEqual([]);
  },
);

it("keeps the typed source preview free of suggestion and interest effects", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ name: suggestion.name })));
  expect(await previewSourceSuggestion(suggestion, options)).toEqual({ name: suggestion.name });
  expect(dispatched).toEqual([]);
});
