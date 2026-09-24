// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UserAccount, UserProvider } from "@/components/user-account";
import { ProfileSettings } from "@/components/profile-settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function app() {
  return render(
    <UserProvider>
      <UserAccount />
      <ProfileSettings />
    </UserProvider>,
  );
}

it.each([null, "reader"])(
  "only shows username guidance before a name is claimed (%s)",
  async (username) => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          Response.json(
            url.endsWith("/me")
              ? { user_id: "one", name: "Reader", email: "reader@example.test", csrf_token: "csrf" }
              : { display_name: "Reader", avatar_url: null, username },
          ),
        ),
      ),
    );
    app();
    const field = await screen.findByRole("textbox", { name: "Username" });
    expect(field).toHaveProperty("readOnly", Boolean(username));
    expect(screen.queryByText("Username claimed. It can’t be changed.")).toBeNull();
    expect(
      screen.queryByText("All details are optional. Changes stay private until you save."),
    ).toBeNull();
    expect(screen.queryByText(/A public profile includes your name/)).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Make my profile public" })).toBeTruthy();
    expect(field.getAttribute("aria-describedby")).toBe(username ? null : "profile-username-help");
    expect(
      Boolean(screen.queryByText("Permanent once saved. 3–30 letters, numbers, _ or -.")),
    ).toBe(!username);
  },
);

function savedProfile(init: RequestInit) {
  const value = JSON.parse(String(init.body));
  // The API returns enriched stack entries, not just the editable write payload.
  return {
    ...value,
    stack: (value.stack ?? []).map((item: { topic_id: string }) => ({
      ...item,
      name: "Python",
      slug: "python",
      logo_url: null,
      status: "active",
    })),
  };
}

it("saves profile overrides with CSRF, updates the navbar and preserves managed identity", async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        url.includes("/topics")
          ? { items: [], next_cursor: null }
          : url.endsWith("/me")
            ? {
                user_id: "one",
                name: "Provider Name",
                email: "user@example.com",
                csrf_token: "csrf",
              }
            : init?.method === "PUT"
              ? savedProfile(init)
              : {
                  display_name: null,
                  avatar_url: null,
                  reading_streak: { current_days: 2 },
                  stack: [{ topic_id: "topic", name: "Python" }],
                },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  app();
  const name = await screen.findByLabelText("Display name");
  expect(name).toHaveProperty("value", "Provider Name");
  expect(screen.queryByText("Managed by your account provider")).toBeNull();
  expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
  fireEvent.change(name, { target: { value: "Python Fan" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Your profile is saved.");
  expect(screen.getByRole("button", { name: "User menu: Python Fan" })).toBeTruthy();
  const [url, options] = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(url).toBe("/api/v1/user/settings/profile");
  expect(options?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
  });
  expect(JSON.parse(String(options?.body))).toEqual({
    display_name: "Python Fan",
    avatar_url: null,
    username: null,
    bio: null,
    location: null,
    about: null,
    links: [],
    stack: [{ topic_id: "topic" }],
    visibility: {
      public: false,
      location: true,
      stack: true,
      heatmap: true,
      achievements: false,
    },
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: Python Fan" }), {
    key: "ArrowDown",
  });
  expect(await screen.findByRole("menuitem", { name: "Profile settings" })).toHaveProperty(
    "href",
    "http://localhost:3000/settings/profile",
  );
  expect(screen.getByRole("menuitem", { name: "Your topics" })).toHaveProperty(
    "href",
    "http://localhost:3000/settings/topics",
  );
  expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
});

it("retains unsaved edits on failure and discards back to the saved profile", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve(
        url.includes("/topics")
          ? Response.json({ items: [], next_cursor: null })
          : init?.method === "PUT"
            ? Response.json({}, { status: 503 })
            : Response.json(
                url.endsWith("/me")
                  ? { user_id: "one", name: "User", csrf_token: "csrf" }
                  : { display_name: "Saved name", avatar_url: null },
              ),
      ),
    ),
  );
  app();
  const name = await screen.findByLabelText("Display name");
  fireEvent.change(name, { target: { value: "New name" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("alert");
  expect(name).toHaveProperty("value", "New name");
  expect(screen.getByRole("button", { name: "User menu: Saved name" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(name).toHaveProperty("value", "Saved name");
});

it("detects link names from URLs, replaces stale labels on save and discards URL edits", async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me")
          ? { user_id: "one", name: "User", csrf_token: "csrf" }
          : init?.method === "PUT"
            ? savedProfile(init)
            : {
                display_name: "Reader",
                avatar_url: null,
                links: [{ url: "https://github.com/reader", label: "Old manual label" }],
              },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  app();
  const url = await screen.findByLabelText("Link 1 URL");
  expect(screen.queryByLabelText("Link 1 label")).toBeNull();
  expect(screen.getByRole("img", { name: "GitHub" })).toBeTruthy();
  expect(screen.queryByText("GitHub")).toBeNull();
  expect(screen.queryByText("Old manual label")).toBeNull();
  expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
  fireEvent.change(url, { target: { value: "https://gitlab.com/reader" } });
  expect(screen.getByRole("img", { name: "GitLab" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(url).toHaveProperty("value", "https://github.com/reader");
  expect(screen.getByRole("img", { name: "GitHub" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add link" }));
  fireEvent.change(screen.getByLabelText("Link 2 URL"), {
    target: { value: "https://reader.dev/work" },
  });
  expect(screen.getByRole("img", { name: "Website" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add link" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Your profile is saved.");
  const [, options] = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(options?.body)).links).toEqual([
    { url: "https://github.com/reader", label: "GitHub" },
    { url: "https://reader.dev/work", label: "Website" },
  ]);
  expect(screen.queryByLabelText("Link 3 URL")).toBeNull();
  fireEvent.change(screen.getByLabelText("Link 1 URL"), {
    target: { value: "https://gitlab.com/reader" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Your profile is saved.");
  const lastSave = fetcher.mock.calls.filter(([, init]) => init?.method === "PUT").at(-1)!;
  expect(JSON.parse(String(lastSave[1]?.body)).links[0]).toEqual({
    url: "https://gitlab.com/reader",
    label: "GitLab",
  });
});

it("does not offer an empty editable profile after a load failure", async () => {
  let fail = true;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url.endsWith("/me")
          ? Response.json({ user_id: "one", name: "User" })
          : fail
            ? Response.json({}, { status: 503 })
            : Response.json({ display_name: null, avatar_url: null }),
      ),
    ),
  );
  app();
  await screen.findByText("Couldn’t load your profile");
  expect(screen.queryByLabelText("Display name")).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByLabelText("Display name");
});

it("adds a technology in one click and keeps category and year optional", async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      url.includes("/topics")
        ? Response.json({
            items: [
              { id: "python", name: "Python", slug: "python", kind: "technology", logo_url: null },
            ],
            next_cursor: null,
          })
        : Response.json(
            url.endsWith("/me")
              ? { user_id: "one", name: "User", csrf_token: "csrf" }
              : init?.method === "PUT"
                ? savedProfile(init)
                : { display_name: null, avatar_url: null },
          ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  app();
  await screen.findByLabelText("Display name");
  fireEvent.change(screen.getByLabelText("Find a technology"), { target: { value: "Python" } });
  fireEvent.click(await screen.findByRole("button", { name: "Add Python" }));
  expect(screen.getByRole("button", { name: "Remove Python" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Usage for Python"), { target: { value: "learning" } });
  fireEvent.change(screen.getByLabelText("Since year for Python"), { target: { value: "2020" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Your profile is saved.");
  const [, options] = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(options?.body)).stack).toEqual([
    { topic_id: "python", section: "learning", since_year: 2020 },
  ]);
});

it("keeps profile sign-in optional and returns to settings after authentication", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(null)));
  app();
  await waitFor(() => expect(screen.getAllByRole("link", { name: "Sign in" })).toHaveLength(2));
  expect(
    screen
      .getAllByRole("link", { name: "Sign in" })
      .some((link) => link.getAttribute("href")?.includes("return_to=%2Fsettings%2Fprofile")),
  ).toBe(true);
  expect(screen.queryByLabelText("Display name")).toBeNull();
});

it("refreshes card statistics without changing editable drafts or dirty state", async () => {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let streak = { current_days: 2, longest_days: 5, total_days: 10 };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        Response.json(
          url.endsWith("/me")
            ? { user_id: "one", name: "Reader", csrf_token: "csrf" }
            : { display_name: "Reader", avatar_url: null, reading_streak: streak },
        ),
      ),
    ),
  );
  app();
  const name = await screen.findByLabelText("Display name");
  const card = () => screen.getByRole("img", { name: /Dev card for/ });
  expect(card().textContent).toContain("day streak: 2.");
  fireEvent.change(name, { target: { value: "Unsaved Reader" } });
  streak = { current_days: 3, longest_days: 6, total_days: 11 };
  now += 61_000;
  fireEvent.focus(window);
  await waitFor(() => expect(card().textContent).toContain("day streak: 3."));
  expect(card().textContent).toContain("best streak: 6.");
  expect(card().textContent).toContain("days reading: 11.");
  expect(name).toHaveProperty("value", "Unsaved Reader");
  expect(screen.getByRole("button", { name: "Download card" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(name).toHaveProperty("value", "Reader");
  expect(card().textContent).toContain("day streak: 3.");
  expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Download card" })).toHaveProperty("disabled", false);
  streak = { current_days: 0, longest_days: 6, total_days: 11 };
  now += 61_000;
  fireEvent.focus(window);
  await waitFor(() => expect(card().textContent).toContain("day streak: 0."));
  expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true);
});
