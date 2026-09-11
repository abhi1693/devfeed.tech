// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { analyticsEventName, trackUserMutation } from "@/lib/analytics";
import { userRequest } from "@/lib/user";
const id = "00000000-0000-4000-8000-000000000001";
afterEach(() => vi.unstubAllGlobals());

it.each([
  [`articles/${id}/like`, "PUT", { liked: true }, "article_like", { article_id: id }],
  [`articles/${id}/like`, "PUT", { liked: false }, "article_unlike", { article_id: id }],
  [`preferences/topics/${id}`, "PUT", { followed: true }, "topic_follow", { topic_id: id }],
  [`preferences/topics/${id}`, "PUT", { followed: false }, "topic_unfollow", { topic_id: id }],
  [`preferences/sources/${id}`, "PUT", { followed: true }, "source_follow", { source_id: id }],
  [`preferences/sources/${id}`, "PUT", { followed: false }, "source_unfollow", { source_id: id }],
  ["preferences", "PUT", { topic_ids: [id] }, "topics_saved", { selected_count: 1 }],
  ["preferences/sources", "PUT", { source_ids: [] }, "sources_saved", { selected_count: 0 }],
  ["settings/feed", "PUT", { view: "compact", content_types: ["article", "news"] }, "feed_settings_saved", { feed_view: "compact", selected_count: 2 }],
  ["settings/appearance", "PUT", { theme: "system", timezone: "Private/Location" }, "appearance_settings_saved", { theme: "system" }],
])("tracks successful %s with explicit parameters", (path, method, result, name, params) => {
  const spy = vi.spyOn(window, "dispatchEvent");
  trackUserMutation(String(path), String(method), result);
  const event = spy.mock.calls[0][0] as CustomEvent;
  expect(event.type).toBe(analyticsEventName);
  expect(event.detail).toEqual({ name, params });
});

it("sends source type without the feed URL, submitted name or response identity", () => {
  const spy = vi.spyOn(window, "dispatchEvent");
  trackUserMutation("sources/suggestions", "POST", { name: "Personal name", id }, JSON.stringify({ source_type: "publisher", feed_url: "https://example.com/private?token=secret", name: "Personal name" }));
  expect((spy.mock.calls[0][0] as CustomEvent).detail).toEqual({ name: "source_suggested", params: { source_type: "publisher" } });
});

it("ignores reads, profile details, lookups, and duplicate backend open telemetry", () => {
  const spy = vi.spyOn(window, "dispatchEvent");
  trackUserMutation("preferences", "GET", { topic_ids: [id] });
  trackUserMutation("settings/profile", "PUT", { name: "Personal name", email: "person@example.com" });
  trackUserMutation("sources/suggestions/preview", "POST", { name: "Feed" });
  trackUserMutation(`articles/${id}/open`, "POST", { opens: 1 });
  expect(spy).not.toHaveBeenCalled();
});

it("emits no success event for failed writes, then emits once on success", async () => {
  const events: CustomEvent[] = [];
  const listener = (event: Event) => events.push(event as CustomEvent);
  window.addEventListener(analyticsEventName, listener);
  try {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Failed" }, { status: 503 })).mockResolvedValueOnce(Response.json({ liked: true }));
    vi.stubGlobal("fetch", fetcher);
    await expect(userRequest(`articles/${id}/like`, { method: "PUT" })).rejects.toThrow();
    expect(events).toHaveLength(0);
    await userRequest(`articles/${id}/like`, { method: "PUT" });
    expect(events.map(event => event.detail)).toEqual([{ name: "article_like", params: { article_id: id } }]);
  } finally { window.removeEventListener(analyticsEventName, listener); }
});
