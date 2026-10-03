// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserMenu } from "@/components/user-menu";
import type { UserProfile } from "@/lib/user";

const state = vi.hoisted(() => ({
  user: { name: "Reader", email: "reader@example.test" },
  profile: null as UserProfile | null,
  profileUnavailable: false,
  signOut: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
afterEach(() => {
  cleanup();
  state.profile = null;
  state.profileUnavailable = false;
});

const publicProfile: UserProfile = {
  display_name: "Reader",
  avatar_url: null,
  username: "reader_1",
  visibility: { public: true, location: false, stack: false, heatmap: false, achievements: false },
};

it("links Your Profile to the public profile above Your Dev Card", async () => {
  state.profile = publicProfile;
  render(<UserMenu />);
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: Reader" }), {
    key: "ArrowDown",
  });
  const profile = await screen.findByRole("menuitem", { name: "Your Profile" });
  expect(profile.getAttribute("href")).toBe("/users/reader_1");
  const items = screen.getAllByRole("menuitem");
  expect(items[0]).toBe(profile);
  expect(items[1]).toBe(screen.getByRole("menuitem", { name: "Your Dev Card" }));
  expect(items[1].getAttribute("href")).toBe("/settings/profile");
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
  fireEvent.keyDown(screen.getByRole("button", { name: "User menu: Reader" }), {
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
