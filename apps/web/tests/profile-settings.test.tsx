// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UserAccount, UserProvider } from "@/components/user-account";
import { ProfileSettings } from "@/components/profile-settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function app() {
  return render(
    <UserProvider>
      <UserAccount />
      <ProfileSettings />
    </UserProvider>,
  );
}

it("saves profile overrides with CSRF, updates the navbar and preserves managed identity", async () => {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      Response.json(
        url.endsWith("/me")
          ? {
              user_id: "one",
              name: "Provider Name",
              email: "user@example.com",
              csrf_token: "csrf",
            }
          : init?.method === "PUT"
            ? JSON.parse(String(init.body))
            : { display_name: null, avatar_url: null },
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
  expect(screen.getByLabelText("Email")).toHaveProperty("value", "user@example.com");
  expect(screen.getByLabelText("Email")).toHaveProperty("readOnly", true);
  const [url, options] = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(url).toBe("/api/v1/user/settings/profile");
  expect(options?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
  });
  expect(JSON.parse(String(options?.body))).toEqual({
    display_name: "Python Fan",
    avatar_url: null,
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

it("retains unsaved edits on failure and resets to the provider defaults", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve(
        init?.method === "PUT"
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
  fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
  expect(name).toHaveProperty("value", "User");
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
