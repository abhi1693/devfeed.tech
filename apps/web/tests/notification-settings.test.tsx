// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationSettings } from "@/components/notification-settings";
import { NotificationInbox } from "@/components/notification-inbox";
import { UserProvider } from "@/components/user-account";
import { NotificationPreferencesProvider } from "@/components/notification-preferences-provider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
let display = { show_badge: true, sound: false }, enabled = true, failDisplay = false, failInbox = false;
let writes: { path: string; body: unknown; csrf: string | null }[];
beforeEach(() => {
  display = { show_badge: true, sound: false }; enabled = true; failDisplay = false; failInbox = false; writes = [];
  vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    const path = new URL(String(input), location.origin).pathname;
    const write = init?.method === "PUT";
    const body = write ? JSON.parse(init.body) : null;
    if (write) writes.push({ path, body, csrf: new Headers(init.headers).get("X-CSRF-Token") });
    if (path.endsWith("auth/me")) return Response.json({ user_id: "alice", name: "Alice", csrf_token: "csrf" });
    if (path.endsWith("settings/profile")) return Response.json({ display_name: null, avatar_url: null });
    if (path.endsWith("settings/notifications")) {
      if (write && failDisplay) return Response.json({}, { status: 503 });
      if (write) display = body;
      return Response.json(display);
    }
    if (path.endsWith("config")) return Response.json({ enabled: true, environment: "users", subscriber_id: "user_alice" });
    if (path.endsWith("preferences")) {
      if (failInbox) return Response.json({}, { status: 503 });
      if (write) enabled = body.preferences[0].enabled;
      return Response.json({ preferences: [{ category: "feed.topic.new", channel: "in_app", enabled }] });
    }
    if (path.endsWith("counts")) return Response.json({ unseen: 2, unread: 2 });
    if (path.endsWith("items")) return Response.json({ items: [], next_cursor: null });
    return Response.json({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function mount() { return render(<UserProvider><NotificationPreferencesProvider><NotificationInbox /><NotificationSettings /></NotificationPreferencesProvider></UserProvider>); }

it("persists display and delivery choices with CSRF, updates the bell and reloads saved values", async () => {
  const user = userEvent.setup();const view = mount();
  await screen.findByRole("button", { name: "Notifications (2 new)" });
  await user.click(await screen.findByRole("checkbox", { name: "Show unread badge" }));
  await user.click(screen.getByRole("checkbox", { name: "Notification sound" }));
  await user.click(screen.getByRole("checkbox", { name: "New articles from followed topics" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Notification settings saved.");
  expect(display).toEqual({ show_badge: false, sound: true });expect(enabled).toBe(false);
  expect(writes).toHaveLength(2);expect(writes.every(write => write.csrf === "csrf")).toBe(true);
  expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy();
  expect(screen.queryByText("You have unsaved changes.")).toBeNull();
  view.unmount();mount();
  const choice = await screen.findByRole("checkbox", { name: "New articles from followed topics" }) as HTMLInputElement;
  expect(choice.checked).toBe(false);expect((screen.getByRole("checkbox", { name: "Notification sound" }) as HTMLInputElement).checked).toBe(true);
});
it("reports partial saves and retains pending display settings for retry", async () => {
  failDisplay = true;const user = userEvent.setup();mount();
  await user.click(await screen.findByRole("checkbox", { name: "Notification sound" }));
  await user.click(screen.getByRole("checkbox", { name: "New articles from followed topics" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("alert");expect(enabled).toBe(false);
  expect((screen.getByRole("checkbox", { name: "Notification sound" }) as HTMLInputElement).checked).toBe(true);
  failDisplay = false;await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Notification settings saved.");expect(display.sound).toBe(true);
});
it("allows display settings during an inbox outage and preserves edits when connection recovers", async () => {
  failInbox = true;const user = userEvent.setup();mount();
  await user.click(await screen.findByRole("checkbox", { name: "Notification sound" }));
  expect((screen.getByRole("checkbox", { name: "New articles from followed topics" }) as HTMLInputElement).disabled).toBe(true);
  failInbox = false;enabled = false;await user.click(screen.getByRole("button", { name: "Retry connection" }));
  await waitFor(() => expect((screen.getByRole("checkbox", { name: "New articles from followed topics" }) as HTMLInputElement).disabled).toBe(false));
  expect((screen.getByRole("checkbox", { name: "New articles from followed topics" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("checkbox", { name: "Notification sound" }) as HTMLInputElement).checked).toBe(true);
  await user.click(screen.getByRole("button", { name: "Save changes" }));await screen.findByText("Notification settings saved.");
  expect(display.sound).toBe(true);
});
it("resets to defaults only when changes are saved", async () => {
  display = { show_badge: false, sound: true };enabled = false;const user = userEvent.setup();mount();
  await user.click(await screen.findByRole("button", { name: "Reset to defaults" }));
  expect(writes).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Save changes" }));await screen.findByText("Notification settings saved.");
  expect(display).toEqual({ show_badge: true, sound: false });expect(enabled).toBe(true);
});
