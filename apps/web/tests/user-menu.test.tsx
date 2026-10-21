// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { UserMenu } from "@/components/user-menu";
import type { UserProfile } from "@/lib/user";

const state = vi.hoisted(() => ({
  user: { name: "Reader" as string | null, email: "reader@example.test" as string | null },
  profile: null as UserProfile | null,
  profileUnavailable: false,
  signOut: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
afterEach(() => {
  cleanup();
  state.profile = null;
  state.profileUnavailable = false;
  state.user.name = "Reader";
  state.user.email = "reader@example.test";
});

const publicProfile: UserProfile = {
  display_name: "Reader",
  avatar_url: null,
  username: "reader_1",
  visibility: { public: true, location: false, stack: false, heatmap: false, achievements: false },
};

it("includes visible avatar initials and the full profile name in the account control", () => {
  state.profile = { ...publicProfile, display_name: "Reader Profile" };
  render(<UserMenu />);
  const button = screen.getByRole("button", { name: "User menu: RP Reader Profile" });
  expect(button.querySelector(".profile-avatar")?.textContent).toBe("RP");
  expect(button.querySelector(".user-menu-name")?.textContent).toBe("Reader Profile");
  expect(button.textContent?.trim()).toBe("RP Reader Profile");
});

it.each([
  ["reader@example.test", "reader@example.test"],
  [null, "Your account"],
])("keeps the account fallback %j without deriving avatar initials from it", (email, label) => {
  state.user.name = null;
  state.user.email = email;
  render(<UserMenu />);
  const button = screen.getByRole("button", { name: `User menu: ${label}` });
  expect(button.querySelector(".profile-avatar")?.textContent).toBe("");
});

it("links Your Profile to the public profile above Your Dev Card", async () => {
  state.profile = publicProfile;
  render(<UserMenu />);
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: R Reader" }), {
    key: "ArrowDown",
  });
  const profile = await screen.findByRole("menuitem", { name: "Your Profile" });
  expect(profile.getAttribute("href")).toBe("/users/reader_1");
  const items = screen.getAllByRole("menuitem");
  expect(items[0]).toBe(profile);
  expect(items[1]).toBe(screen.getByRole("menuitem", { name: "Your Dev Card" }));
  expect(items[1].getAttribute("href")).toBe("/settings/profile");
});

it("makes Appearance reachable from the avatar menu with keyboard navigation", async () => {
  const user = userEvent.setup();
  render(<UserMenu />);
  const trigger = screen.getByRole("button", { name: "User menu: R Reader" });
  trigger.focus();
  await user.keyboard("{ArrowDown}");
  const appearance = await screen.findByRole("menuitem", { name: "Appearance" });
  expect(appearance.getAttribute("href")).toBe("/settings/appearance");
  await user.keyboard("a");
  await waitFor(() => expect(document.activeElement).toBe(appearance));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  expect(screen.queryByRole("menuitem", { name: "Appearance" })).toBeNull();
});

it("omits Leaderboard and Notifications from the avatar menu", async () => {
  render(<UserMenu />);
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: R Reader" }), {
    key: "ArrowDown",
  });
  await screen.findByRole("menu");
  expect(screen.queryByRole("menuitem", { name: "Leaderboard" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Notifications" })).toBeNull();
  expect(screen.getByRole("menuitem", { name: "Appearance" }).getAttribute("href")).toBe(
    "/settings/appearance",
  );
});

it.each([
  { profile: null, profileUnavailable: false },
  { profile: { ...publicProfile, username: null }, profileUnavailable: false },
  {
    profile: { ...publicProfile, visibility: { ...publicProfile.visibility!, public: false } },
    profileUnavailable: false,
  },
  { profile: publicProfile, profileUnavailable: true },
])("offers username claiming when a public profile is unavailable: %j", async (profileState) => {
  Object.assign(state, profileState);
  render(<UserMenu />);
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: R Reader" }), {
    key: "ArrowDown",
  });
  const claim = await screen.findByRole("menuitem", { name: "Claim your username" });
  expect(claim.getAttribute("aria-disabled")).not.toBe("true");
  expect(claim.getAttribute("href")).toBe("/settings/profile");
  expect(screen.queryByRole("menuitem", { name: "Your Profile" })).toBeNull();
  expect(screen.getAllByRole("menuitem")[0]).toBe(claim);
  expect(screen.getByRole("menuitem", { name: "Your Dev Card" }).getAttribute("href")).toBe(
    "/settings/profile",
  );
});

it("includes noninteractive streak progress without changing the account menu's first action", async () => {
  state.profile = {
    ...publicProfile,
    reading_streak: {
      current_days: 11,
      longest_days: 11,
      total_days: 24,
      last_read_date: new Date().toISOString().slice(0, 10),
    },
  };
  render(<UserMenu />);
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: R Reader" }), {
    key: "ArrowDown",
  });
  const label = await screen.findByText("Reading streak");
  expect(label.closest('[role="menu"]')).toBeTruthy();
  expect(screen.getByText("11 days")).toBeTruthy();
  expect(screen.getByText("3 days to the next milestone of 14 days.")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getAllByRole("menuitem")[0]).toBe(
    screen.getByRole("menuitem", { name: "Your Profile" }),
  );
});
