// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppearanceSettings } from "@/components/appearance-settings";
import { ThemeToggle } from "@/components/theme-toggle";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { UserProvider } from "@/components/user-account";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { guestFeedViewsKey } from "@/lib/feed-view-scope";

const defaultFeed = { view: "cards", content_types: ["news"], languages: ["en"] };
const defaultAppearance = {
  theme: "system",
  timezone: "local",
  date_format: "locale",
  time_format: "system",
};
function Providers({ children }: { children: ReactNode }) {
  return (
    <UserProvider>
      <ThemePreferencesProvider>
        <FeedPreferencesProvider>{children}</FeedPreferencesProvider>
      </ThemePreferencesProvider>
    </UserProvider>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});
it("loads account theme and synchronizes the settings dropdown and navbar cycle after saving", async () => {
  let theme = "dark";
  let fail = false;
  const fetcher = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me"))
      return Promise.resolve(Response.json({ user_id: "user-a", csrf_token: "csrf" }));
    if (url.endsWith("settings/feed")) return Promise.resolve(Response.json(defaultFeed));
    if (url.includes("settings/feed-view?"))
      return Promise.resolve(Response.json({ view: "cards" }));
    if (init?.method === "PUT") {
      if (fail) return Promise.resolve(Response.json({}, { status: 503 }));
      theme = JSON.parse(String(init.body)).theme;
    }
    return Promise.resolve(Response.json({ theme }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <Providers>
      <ThemeToggle />
      <AppearanceSettings />
    </Providers>,
  );
  const dropdown = await screen.findByRole("combobox", { name: "Theme" });
  await waitFor(() => expect(document.documentElement.classList.contains("dark")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Theme: Dark. Switch to system theme" }));
  await waitFor(() => expect(theme).toBe("system"));
  await screen.findByRole("button", { name: "Theme: System. Switch to light theme" });
  expect(dropdown.textContent).toContain("System");
  fireEvent.click(dropdown);
  fireEvent.click(screen.getByRole("option", { name: "Light" }));
  await screen.findByRole("button", { name: "Theme: Light. Switch to dark theme" });
  expect(localStorage.getItem("devfeed:theme")).toBe("light");
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")?.[1];
  expect(write?.headers).toEqual({ "Content-Type": "application/json", "X-CSRF-Token": "csrf" });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Theme: Light. Switch to dark theme" }));
  await screen.findAllByRole("alert");
  expect(theme).toBe("light");
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});

it.each([
  ["appearance", "Appearance saved.", "Appearance"],
  ["layout", "Article layout saved.", "Article feed layout"],
])(
  "confirms %s saves with a toast only after persistence succeeds",
  async (kind, message, region) => {
    let finish: ((response: Response) => void) | undefined;
    let payload: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("auth/me"))
          return Response.json({ user_id: "reader", csrf_token: "csrf" });
        if (init?.method === "PUT") {
          payload = JSON.parse(String(init.body));
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        if (url.includes("settings/feed-view?")) return Response.json({ view: "cards" });
        return Response.json(url.endsWith("settings/feed") ? defaultFeed : defaultAppearance);
      }),
    );
    render(
      <Providers>
        <AppearanceSettings />
      </Providers>,
    );
    const theme = await screen.findByRole("combobox", { name: "Theme" });
    await screen.findByRole("radio", { name: "List" });
    expect(screen.queryByText("Changes save automatically.")).toBeNull();
    expect(screen.queryByText(message)).toBeNull();
    function choose(failing = false) {
      if (kind === "layout") {
        fireEvent.click(screen.getByRole("radio", { name: failing ? "Grid" : "List" }));
      } else {
        fireEvent.click(theme);
        fireEvent.click(screen.getByRole("option", { name: failing ? "Dark" : "Light" }));
      }
    }
    choose();
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    expect(screen.getByRole("region", { name: region }).getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText(message)).toBeNull();
    expect(screen.queryByText("Saving…")).toBeNull();
    await act(async () => finish!(Response.json(payload)));
    await screen.findByText(message);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    await waitFor(() => expect(screen.queryByText(message)).toBeNull());
    finish = undefined;
    choose(true);
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    await act(async () => finish!(Response.json({}, { status: 503 })));
    await screen.findByRole("alert");
    expect(screen.queryByText(message)).toBeNull();
    expect(screen.queryByText("Changes save automatically.")).toBeNull();
  },
);

it("saves date preferences without changing the theme and applies them to article dates", async () => {
  const { UserDate } = await import("@/components/user-date");
  let preferences = { theme: "dark", timezone: "UTC", date_format: "iso", time_format: "24" };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("auth/me"))
        return Promise.resolve(Response.json({ user_id: "user-a", csrf_token: "csrf" }));
      if (url.endsWith("settings/feed")) return Promise.resolve(Response.json(defaultFeed));
      if (url.includes("settings/feed-view?"))
        return Promise.resolve(Response.json({ view: "cards" }));
      if (init?.method === "PUT") preferences = JSON.parse(String(init.body));
      return Promise.resolve(Response.json(preferences));
    }),
  );
  render(
    <Providers>
      <ThemeToggle />
      <AppearanceSettings />
      <UserDate value="2026-09-10T23:30:00Z" />
    </Providers>,
  );
  const zone = await screen.findByRole("combobox", { name: "Timezone" });
  expect(screen.getByText("2026-09-10").getAttribute("title")).toBe("2026-09-10, 23:30");
  fireEvent.click(zone);
  fireEvent.change(screen.getByPlaceholderText("Search timezone…"), {
    target: { value: "Kolkata" },
  });
  fireEvent.click(await screen.findByRole("option", { name: "Asia/Kolkata" }));
  await waitFor(() => expect(preferences.timezone).toBe("Asia/Kolkata"));
  await waitFor(() =>
    expect(screen.getByText("2026-09-11").getAttribute("title")).toBe("2026-09-11, 05:00"),
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Time format" }));
  fireEvent.click(screen.getByRole("option", { name: "12 hour" }));
  await waitFor(() => expect(preferences.time_format).toBe("12"));
  fireEvent.click(screen.getByRole("button", { name: "Theme: Dark. Switch to system theme" }));
  await waitFor(() =>
    expect(preferences).toEqual({
      theme: "system",
      timezone: "Asia/Kolkata",
      date_format: "iso",
      time_format: "12",
    }),
  );
});

it("lets guests choose a feed's layout in Appearance without loading private settings", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("auth/me")) return Response.json({}, { status: 401 });
    throw new Error(`Unexpected private request: ${String(input)}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <Providers>
      <AppearanceSettings />
    </Providers>,
  );
  const layout = within(await screen.findByRole("region", { name: "Article feed layout" }));
  const grid = await layout.findByRole("radio", { name: "Grid" });
  await waitFor(() => expect((grid as HTMLInputElement).disabled).toBe(false));
  const user = userEvent.setup();
  await user.click(grid);
  await user.keyboard("[ArrowRight]");
  expect((layout.getByRole("radio", { name: "List" }) as HTMLInputElement).checked).toBe(true);
  expect(JSON.parse(localStorage.getItem(guestFeedViewsKey)!).views).toEqual({ latest: "compact" });
  expect(localStorage.getItem("devfeed:feed-view:guest")).toBeNull();
  await screen.findByText("Article layout saved.");
  expect(layout.queryByRole("button", { name: /save/i })).toBeNull();
  expect(fetcher.mock.calls.every(([url]) => String(url).endsWith("auth/me"))).toBe(true);
});

it("keeps a guest layout usable without claiming it was saved when storage is blocked", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({}, { status: 401 })),
  );
  render(
    <Providers>
      <AppearanceSettings />
    </Providers>,
  );
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() => expect((list as HTMLInputElement).disabled).toBe(false));
  const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  try {
    await act(async () => fireEvent.click(list));
    expect((list as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByText("Article layout saved.")).toBeNull();
  } finally {
    storage.mockRestore();
  }
});

it("keeps layout settings usable when theme settings fail", async () => {
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("auth/me")) return Response.json({ user_id: "reader", csrf_token: "csrf" });
      if (url.endsWith("settings/appearance")) return Response.json({}, { status: 503 });
      if (url.includes("settings/feed-view?")) {
        if (init?.method === "PUT") {
          writes.push(url);
          return Response.json(JSON.parse(String(init.body)));
        }
        return Response.json({ view: "compact" });
      }
      if (url.endsWith("settings/feed")) {
        if (init?.method === "PUT") {
          writes.push(url);
          return Response.json(JSON.parse(String(init.body)));
        }
        return Response.json({ ...defaultFeed, view: "compact" });
      }
      return Response.json({});
    }),
  );
  render(
    <Providers>
      <AppearanceSettings />
    </Providers>,
  );
  await screen.findByRole("heading", { name: "Couldn’t load appearance settings" });
  const layout = within(screen.getByRole("region", { name: "Article feed layout" }));
  const list = await layout.findByRole("radio", { name: "List" });
  await waitFor(() => expect((list as HTMLInputElement).checked).toBe(true));
  fireEvent.click(layout.getByRole("radio", { name: "Grid" }));
  await waitFor(() => expect(writes).toEqual(["/api/v1/user/settings/feed-view?feed=my_feed"]));
  await waitFor(() =>
    expect((layout.getByRole("radio", { name: "Grid" }) as HTMLInputElement).checked).toBe(true),
  );
});

it("retries layout settings independently while keeping theme settings available", async () => {
  let feedReads = 0;
  let theme = "dark";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("auth/me")) return Response.json({ user_id: "reader", csrf_token: "csrf" });
      if (url.endsWith("settings/feed")) {
        if (++feedReads === 1) return Response.json({}, { status: 503 });
        return Response.json({ ...defaultFeed, view: "compact" });
      }
      if (url.includes("settings/feed-view?")) return Response.json({ view: "compact" });
      if (url.endsWith("settings/appearance")) {
        if (init?.method === "PUT") theme = JSON.parse(String(init.body)).theme;
        return Response.json({ theme });
      }
      return Response.json({});
    }),
  );
  render(
    <Providers>
      <AppearanceSettings />
    </Providers>,
  );
  const themeControl = await screen.findByRole("combobox", { name: "Theme" });
  const unavailable = within(await screen.findByRole("region", { name: "Article feed layout" }));
  await unavailable.findByRole("heading", { name: "Couldn’t load article layout" });
  fireEvent.click(unavailable.getByRole("button", { name: "Retry" }));
  const layout = within(await screen.findByRole("region", { name: "Article feed layout" }));
  await waitFor(() =>
    expect((layout.getByRole("radio", { name: "List" }) as HTMLInputElement).checked).toBe(true),
  );
  fireEvent.click(themeControl);
  fireEvent.click(screen.getByRole("option", { name: "Light" }));
  await waitFor(() => expect(theme).toBe("light"));
  expect(feedReads).toBe(2);
});
