// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationInbox } from "@/components/notification-inbox";
import { UserProvider } from "@/components/user-account";
import { notificationArticle, inboxPath } from "@/lib/inbox";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const article = "00000000-0000-0000-0000-000000000001";
class Events {
  static instances: Events[] = [];
  closed = false;
  constructor() { Events.instances.push(this); }
  addEventListener() {}
  close() { this.closed = true; }
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); Events.instances = []; });

it("does not load an inbox for anonymous visitors", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(null));
  vi.stubGlobal("fetch", fetcher);
  render(<UserProvider><NotificationInbox /></UserProvider>);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: /Notifications/ })).toBeNull();
});

it("uses the shared inbox, marks seen and read with CSRF, opens an article modal and closes on sign out", async () => {
  const requests: { path: string; headers: Headers; method: string }[] = [];
  vi.stubGlobal("EventSource", Events);
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    const path = new URL(String(input), location.origin).pathname;
    requests.push({ path, headers: new Headers(init?.headers), method: init?.method ?? "GET" });
    if (path.endsWith("auth/me")) return Response.json({ user_id: "user-a", csrf_token: "csrf" });
    if (path.endsWith("settings/profile")) return Response.json({ display_name: null, avatar_url: null });
    if (path.endsWith("config")) return Response.json({ enabled: true, environment: "users", subscriber_id: "user_a" });
    if (path.endsWith("counts")) return Response.json({ unread: 1, unseen: 1 });
    if (path.endsWith("items")) return Response.json({ items: [{ id: "notif_" + "a".repeat(26), source: "notification", category: "feed.topic.new", payload: { title: "Python update", body: "New in your topics", action_url: `/articles/${article}` }, occurred_at: "2026-09-11T10:00:00Z", read: false, archived: false }], next_cursor: null });
    if (path.endsWith("preferences")) return Response.json({ preferences: [] });
    return Response.json({});
  }));
  render(<UserProvider><NotificationInbox /></UserProvider>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Notifications (1 new)" }));
  expect(await screen.findByRole("tab", { name: /Unread/ })).toBeTruthy();
  expect(requests.some(r => r.path.endsWith("seen-all") && r.headers.get("x-csrf-token") === "csrf")).toBe(true);
  await user.click(await screen.findByText("Python update"));
  expect(router.push).toHaveBeenCalledWith(`/articles/${article}`, { scroll: false });
  expect(requests.some(r => r.path.endsWith("/read") && r.method === "POST" && r.headers.get("x-csrf-token") === "csrf")).toBe(true);
  expect(requests.filter(r => r.path.includes("chimely")).every(r => r.path.startsWith(inboxPath))).toBe(true);
  fireEvent(window, new Event("devfeed:user-session-expired"));
  expect(screen.queryByRole("button", { name: /Notifications/ })).toBeNull();
  expect(Events.instances.every(event => event.closed)).toBe(true);
});

it("only opens local article URLs", () => {
  expect(notificationArticle(`/articles/${article}`)).toBe(`/articles/${article}`);
  expect(notificationArticle("/articles/docker-image-guide-42")).toBe("/articles/docker-image-guide-42");
  for (const url of ["//evil.test", "javascript:alert(1)", "/preferences", "/articles/../admin", null]) expect(notificationArticle(url)).toBeNull();
});
