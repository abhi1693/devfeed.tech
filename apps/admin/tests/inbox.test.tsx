// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationInbox } from "@/components/organisms/notification-inbox";
import { inboxAction, inboxPath } from "@/lib/inbox";
import { adminNotificationConfig } from "@/lib/api/generated/admin";
import { returnToLogin } from "@/lib/api/client";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/generated/admin", () => ({ adminNotificationConfig: vi.fn() }));
vi.mock("@/lib/api/client", async original => ({ ...await original<typeof import("@/lib/api/client")>(), returnToLogin: vi.fn() }));

class Events {
  static instances: Events[] = [];
  listeners = new Map<string, (event: object) => void>();
  closed = false;
  constructor(public url: string) { Events.instances.push(this); }
  addEventListener(type: string, listener: (event: object) => void) { this.listeners.set(type, listener); }
  close() { this.closed = true; }
  emit(type: string) { this.listeners.get(type)?.({ data: "{}" }); }
}
const item = { id: "bcast_" + "a".repeat(26), source: "broadcast", category: "jobs.ingestion", payload: { title: "Feed ingestion failed", body: "Open the run for logs.", severity: "error", action_url: "/ingestion-jobs/run-1" }, occurred_at: "2026-09-07T00:00:00Z", read: false, archived: false };
let requests: { path: string; method: string; headers: Headers }[];
let items = [item], unseen = 1;

beforeEach(() => {
  vi.clearAllMocks(); Events.instances = []; requests = []; items = [{ ...item }]; unseen = 1;
  vi.stubGlobal("EventSource", Events);
  vi.mocked(adminNotificationConfig).mockResolvedValue({ enabled: true, environment: "admin-prod", subscriber_id: "admin_alice" });
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    const url = new URL(String(input), location.origin), method = init?.method ?? "GET";
    requests.push({ path: url.pathname, method, headers: new Headers(init?.headers) });
    if (url.pathname.endsWith("/counts")) return Response.json({ unread: items.filter(row => !row.read).length, unseen });
    if (url.pathname.endsWith("/items")) return Response.json({ items, next_cursor: null });
    if (url.pathname.endsWith("/seen-all")) { unseen = 0; return Response.json({ unseen: 0 }); }
    if (url.pathname.endsWith("/read")) { items[0] = { ...items[0], read: true }; return Response.json({}); }
    if (url.pathname.endsWith("/preferences")) return Response.json({ preferences: [] });
    return Response.json({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("persistent Chimely inbox (separate from toasts)", () => {
  it("loads unseen count, opens on click only, marks seen with CSRF, and links to run", async () => {
    const user = userEvent.setup(); render(<NotificationInbox csrfToken="csrf" />);
    const bell = await screen.findByRole("button", { name: "Notifications (1 new)" });
    await user.hover(bell); expect(screen.queryByText("Feed ingestion failed")).toBeNull();
    await user.click(bell);
    await screen.findByText("Feed ingestion failed");
    expect(screen.getByText("Open the run for logs.")).toBeTruthy();
    expect(requests.some(r => r.path.endsWith("/seen-all") && r.headers.get("x-csrf-token") === "csrf")).toBe(true);
    await user.click(screen.getByText("Feed ingestion failed"));
    expect(router.push).toHaveBeenCalledWith("/jobs/ingestion/run-1");
    expect(requests.some(r => r.path.endsWith("/read") && r.method === "POST")).toBe(true);
    expect(requests.every(r => r.path.startsWith(inboxPath))).toBe(true);
  });

  it("opens topic research notifications at the topic run URL", async () => {
    items = [{ ...item, category: "jobs.topic-analysis", payload: { ...item.payload, title: "Topic research completed", severity: "success", action_url: "/jobs/analysis/topics/topic-run" } }];
    const user = userEvent.setup(); render(<NotificationInbox csrfToken="csrf" />);
    await user.click(await screen.findByRole("button", { name: "Notifications (1 new)" }));
    await user.click(await screen.findByText("Topic research completed"));
    expect(router.push).toHaveBeenCalledWith("/jobs/analysis/topics/topic-run");
  });

  it("refreshes on real-time hints and closes its stream on unmount", async () => {
    const { unmount } = render(<NotificationInbox csrfToken="csrf" />);
    await screen.findByRole("button", { name: "Notifications (1 new)" });
    unseen = 3;
    act(() => Events.instances.at(-1)!.emit("hint"));
    await screen.findByRole("button", { name: "Notifications (3 new)" });
    unmount(); expect(Events.instances.every(event => event.closed)).toBe(true);
  });

  it("closes the stream and stops refresh calls on blur, then reconnects once on focus", async () => {
    vi.useFakeTimers();
    try {
      const focus = vi.spyOn(document, "hasFocus").mockReturnValue(true);
      await act(async () => { render(<NotificationInbox csrfToken="csrf" />); });
      expect(screen.getByRole("button", { name: "Notifications (1 new)" })).toBeTruthy();
      const before = requests.length;
      const streams = Events.instances.length;
      focus.mockReturnValue(false);
      await act(async () => { window.dispatchEvent(new Event("blur")); });
      expect(Events.instances.every(event => event.closed)).toBe(true);
      await act(async () => { await vi.advanceTimersByTimeAsync(120000); });
      expect(requests).toHaveLength(before);
      focus.mockReturnValue(true);
      await act(async () => { window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); });
      expect(Events.instances).toHaveLength(streams + 1);
      expect(requests.length).toBeGreaterThan(before);
    } finally { cleanup(); vi.useRealTimers(); }
  });

  it("hides the bell without requesting Chimely when disabled", async () => {
    vi.mocked(adminNotificationConfig).mockResolvedValue({ enabled: false });
    render(<NotificationInbox csrfToken="csrf" />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "Notifications" })).toBeNull();
    expect(requests).toEqual([]); expect(Events.instances).toHaveLength(0);
  });

  it("does not flash an unconfigured bell while configuration loads", () => {
    vi.mocked(adminNotificationConfig).mockReturnValue(new Promise(() => {}));
    render(<NotificationInbox csrfToken="csrf" />);
    expect(screen.queryByRole("button", { name: "Notifications" })).toBeNull();
  });

  it("allows retrying configuration failure without toast spam", async () => {
    vi.mocked(adminNotificationConfig).mockRejectedValueOnce(new Error("offline"));
    render(<NotificationInbox csrfToken="csrf" />);
    fireEvent.click(await screen.findByRole("button", { name: "Notifications" }));
    await screen.findByText("Notifications are temporarily unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("button", { name: "Notifications (1 new)" });
  });

  it("ends the local session flow when an inbox call is no longer authorized", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status: 401 })));
    render(<NotificationInbox csrfToken="csrf" />);
    await waitFor(() => expect(returnToLogin).toHaveBeenCalled());
  });

  it.each(["//evil.example", "javascript:alert(1)", "https://evil.example", "/\\evil", undefined, 123])("blocks unsafe notification actions %s", value => {
    expect(inboxAction(value)).toBeNull();
  });
});
