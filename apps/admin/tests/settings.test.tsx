// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminSession } from "@/components/molecules/admin-session";
import { ThemeToggle } from "@/components/molecules/theme-toggle";
import { notifyFailure } from "@/lib/notifications";
import { SettingsPage } from "@/components/organisms/settings-page";
import { DataTable } from "@/components/molecules/data-table";
import { UserMenu } from "@/components/molecules/user-menu";
import { DateTime } from "@/components/molecules/date-time";
import { defaultSettings, formatDate, normalizeSettings } from "@/lib/settings";
import { useTableQuery } from "@/lib/use-table-query";
import { notificationChoices, notificationPreferences } from "@/lib/notification-preferences";
import { adminSettingsGet, adminSettingsProfile, adminSettingsAppearance, adminSettingsTable, adminNotificationConfig } from "@/lib/api/generated/admin";
import { useSettings } from "@/lib/use-settings";
import type { SettingsSection } from "@/lib/settings";

const navigation = vi.hoisted(() => ({ raw: "" }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(navigation.raw), usePathname: () => "/settings/profile" }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn(), warning: vi.fn() }, notifyFailure: vi.fn() }));
vi.mock("@/lib/api/generated/admin", () => ({ adminSettingsGet: vi.fn(), adminSettingsProfile: vi.fn(), adminSettingsAppearance: vi.fn(), adminSettingsDefaults: vi.fn(), adminSettingsNotifications: vi.fn(), adminSettingsTable: vi.fn(), adminSettingsTablesReset: vi.fn(), adminNotificationConfig: vi.fn() }));
const admin = { name: "Alex", email: "alex@example.com", subject: "one", issuer: "fixture", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" };
let settings = normalizeSettings({});
beforeEach(() => {
  vi.clearAllMocks(); settings = normalizeSettings({}); navigation.raw = "";
  vi.mocked(adminSettingsGet).mockImplementation(async () => settings);
  vi.mocked(adminSettingsProfile).mockImplementation(async value => { settings = normalizeSettings({ ...settings, profile: value }); return settings; });
  vi.mocked(adminSettingsAppearance).mockImplementation(async value => { settings = normalizeSettings({ ...settings, appearance: value }); return settings; });
  vi.mocked(adminSettingsTable).mockImplementation(async (key, value) => { settings = normalizeSettings({ ...settings, tables: { ...settings.tables, [key]: { ...settings.tables[key], ...value, columns: value.columns ?? settings.tables[key]?.columns, query: value.query ?? settings.tables[key]?.query } } }); return settings; });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
function page(section: SettingsSection) { return render(<AdminSession admin={admin} settings={settings}><UserMenu admin={admin} /><SettingsPage section={section} /></AdminSession>); }
function select(label: string, option: string) { fireEvent.click(screen.getByRole("combobox", { name: label })); fireEvent.click(screen.getByRole("option", { name: option })); }

it("saves profile with CSRF, updates the shared menu and restores it after remount", async () => {
  const view = page("profile");
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Editor" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("button", { name: "User menu: Editor" });
  expect(adminSettingsProfile).toHaveBeenCalledWith({ display_name: "Editor", avatar_url: null }, { headers: { "X-CSRF-Token": "test-csrf" } });
  view.unmount(); page("profile");
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("Editor");
  fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
  expect(adminSettingsProfile).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("button", { name: "User menu: Alex" });
});

it("retains a failed save and unsaved edits when settings refresh in another tab", async () => {
  vi.mocked(adminSettingsProfile).mockRejectedValueOnce(new Error("Offline"));
  page("profile");
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Draft" } });
  settings = normalizeSettings({ profile: { display_name: "Other tab" } });
  fireEvent(window, new Event("focus"));
  await screen.findByRole("button", { name: "User menu: Other tab" });
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("Draft");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("Draft");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("button", { name: "User menu: Draft" });
});

it("applies appearance to the shared document and formats dates in the chosen timezone", async () => {
  page("appearance"); select("Theme", "Dark"); select("Table density", "Compact");
  fireEvent.click(screen.getByRole("checkbox", { name: /Reduce animations/ }));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
  expect(document.documentElement.dataset.density).toBe("compact");
  expect(document.documentElement.dataset.reduceMotion).toBe("true");
  const display = { ...defaultSettings.appearance, timezone: "Asia/Kolkata", date_format: "iso" as const, time_format: "24" as const };
  expect(formatDate("2026-09-10T23:30:00Z", display)).toBe("2026-09-11, 05:00");
  expect(formatDate("invalid", display)).toBe("—");
});

it("scopes preferences to an account even when the surrounding layout survives", () => {
  const view = render(<AdminSession admin={admin} settings={{ profile: { display_name: "One" } }}><UserMenu admin={admin} /></AdminSession>);
  expect(screen.getByRole("button", { name: "User menu: One" })).toBeTruthy();
  view.rerender(<AdminSession admin={{ ...admin, subject: "two" }} settings={{}}><UserMenu admin={admin} /></AdminSession>);
  expect(screen.getByRole("button", { name: "User menu: Alex" })).toBeTruthy();
});

it("restores filters, lets explicit URLs and clearing win, and persists column choices", async () => {
  settings = normalizeSettings({ defaults: { page_size: 50 }, tables: { topics: { query: { q: "AI", sort: "name" }, columns: { kind: false } } } });
  function Reader() { const query = useTableQuery("topics"); return <output>{query.toString()}</output>; }
  const view = render(<AdminSession admin={admin} settings={settings}><Reader /><DataTable label="Topics" preferenceKey="topics" columnChoices data={[{ id: "one", kind: "Technology" }]} getRowId={row => row.id} columns={[{ id: "kind", header: "Kind", cell: ({ row }) => row.original.kind }, { id: "actions", header: "Actions", enableHiding: false }]} /></AdminSession>);
  expect(screen.getByRole("status").textContent).toBe("q=AI&sort=name&limit=50");
  expect(screen.queryByText("Technology")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Columns" })); fireEvent.click(screen.getByRole("checkbox", { name: "Kind" }));
  await screen.findByText("Technology");
  await waitFor(() => expect(settings.tables.topics.columns?.kind).toBe(true));
  navigation.raw = "offset=0";
  view.rerender(<AdminSession admin={admin} settings={settings}><Reader /></AdminSession>);
  expect(screen.getByRole("status").textContent).toBe("offset=0&limit=50");
  await waitFor(() => expect(settings.tables.topics.query).toEqual({}));
});

it("cannot let an old focus response overwrite a newer save", async () => {
  let complete!: (value: typeof settings) => void;
  vi.mocked(adminSettingsGet).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  function Editor() { const { save } = useSettings(); return <button onClick={() => void save("profile", { display_name: "New", avatar_url: null })}>Change</button>; }
  render(<AdminSession admin={admin} settings={settings}><Editor /><UserMenu admin={admin} /><DateTime value="2026-09-10T00:00:00Z" /></AdminSession>);
  fireEvent(window, new Event("focus")); await waitFor(() => expect(adminSettingsGet).toHaveBeenCalled());
  fireEvent.click(screen.getByText("Change")); await screen.findByRole("button", { name: "User menu: New" });
  await act(async () => complete(normalizeSettings({ profile: { display_name: "Stale" } })));
  expect(screen.getByRole("button", { name: "User menu: New" })).toBeTruthy();
});

it("preserves earlier category opt-outs and separates failure, retry and completion preferences", () => {
  const choices = notificationChoices([{ category: "jobs.analysis", channel: "in_app", enabled: false }]);
  expect(choices["jobs.analysis.error"]).toBe(false);
  choices["jobs.analysis.error"] = true;
  const payload = notificationPreferences(choices);
  expect(payload.find(row => row.category === "jobs.analysis")?.enabled).toBe(true);
  expect(payload.find(row => row.category === "jobs.analysis.success")?.enabled).toBe(false);
  expect(payload.find(row => row.category === "jobs.relationship-research.error")?.enabled).toBe(true);
});

it("loads and saves actual Chimely preferences through the session proxy with CSRF", async () => {
  vi.mocked(adminNotificationConfig).mockResolvedValue({ enabled: true, environment: "test", subscriber_id: "admin-fixture" });
  let payload: { preferences: { category: string; enabled: boolean }[] } | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input, init) => {
    const url = new URL(String(input), location.origin);
    if (init?.method === "PUT") { expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-csrf"); payload = JSON.parse(init.body as string); }
    if (url.pathname.endsWith("/preferences")) return Response.json(payload ?? { preferences: [] });
    if (url.pathname.endsWith("/counts")) return Response.json({ unread: 0, unseen: 0 });
    return Response.json({ items: [], next_cursor: null });
  }));
  const { adminSettingsNotifications } = await import("@/lib/api/generated/admin");
  vi.mocked(adminSettingsNotifications).mockImplementation(async value => ({ ...settings, notifications: value }));
  page("notifications");
  fireEvent.click(await screen.findByRole("checkbox", { name: "Article analysis: Completions" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(payload?.preferences.find(row => row.category === "jobs.analysis.success")?.enabled).toBe(false));
  await waitFor(() => expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(true));
});

it("inherits earlier category opt-outs into missing event choices without overwriting explicit choices", async () => {
  const { inheritNotificationPreferences } = await import("@/lib/notification-preferences");
  const existing = [{ category: "jobs.topic-analysis", channel: "in_app" as const, enabled: false }, { category: "jobs.topic-analysis.error", channel: "in_app" as const, enabled: true }];
  const client = { getPreferences: vi.fn().mockResolvedValue(existing), setPreferences: vi.fn().mockResolvedValue(existing) };
  await inheritNotificationPreferences(client);
  expect(client.setPreferences).toHaveBeenCalledExactlyOnceWith([
    { category: "jobs.topic-analysis.warning", channel: "in_app", enabled: false },
    { category: "jobs.topic-analysis.success", channel: "in_app", enabled: false },
    { category: "jobs.relationship-research.error", channel: "in_app", enabled: false },
    { category: "jobs.relationship-research.warning", channel: "in_app", enabled: false },
    { category: "jobs.relationship-research.success", channel: "in_app", enabled: false },
  ]);
});


it("toggles the effective system theme and saves without changing other appearance settings", async () => {
  vi.stubGlobal("matchMedia", () => Object.assign(new EventTarget(), { matches: true }));
  settings = normalizeSettings({ appearance: { ...defaultSettings.appearance, theme: "system", density: "compact", timezone: "Asia/Kolkata" } });
  render(<AdminSession admin={admin} settings={settings}><ThemeToggle /></AdminSession>);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(false));
  expect(adminSettingsAppearance).toHaveBeenCalledWith(
    { ...settings.appearance, theme: "light", density: "compact", timezone: "Asia/Kolkata" },
    { headers: { "X-CSRF-Token": admin.csrf_token } },
  );
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
});
it("keeps the saved theme and reports failures when the header toggle cannot save", async () => {
  settings = normalizeSettings({ appearance: { ...defaultSettings.appearance, theme: "light" } });
  const error = new Error("offline");
  vi.mocked(adminSettingsAppearance).mockRejectedValue(error);
  render(<AdminSession admin={admin} settings={settings}><ThemeToggle /></AdminSession>);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(notifyFailure).toHaveBeenCalledWith(error, "Couldn’t save theme"));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(screen.getByRole("button")).toHaveProperty("disabled", false);
});
