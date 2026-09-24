// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { PublicUserProfile } from "@/components/public-user-profile";
import type { UserProfile } from "@/lib/user";
const state = vi.hoisted(() => ({ profile: null as { username: string } | null }));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
afterEach(() => {
  cleanup();
  state.profile = null;
});
const profile: UserProfile = {
  username: "reader",
  display_name: "Public Reader",
  avatar_url: null,
  bio: "Building useful things.",
  location: "Bengaluru",
  about: "I build developer tools.\nI enjoy learning in public.",
  links: [
    { url: "https://github.com/reader", label: "GitHub" },
    { url: "javascript:alert(1)", label: "Unsafe" },
  ],
  stack: [
    {
      topic_id: "rust",
      name: "Rust",
      slug: "rust",
      logo_url: null,
      status: "active",
      section: "learning",
      since_year: 2024,
    },
  ],
  reading_streak: { current_days: 3, longest_days: 8, total_days: 20, last_read_date: null },
};
it("shows the profile's identity, about, links, stack, and reading calendar", () => {
  render(
    <PublicUserProfile
      profile={profile}
      activity={{
        year: 2026,
        timezone: "UTC",
        days: [
          { date: "2026-01-01", article_count: 3 },
          { date: "2026-01-02", article_count: 0 },
        ],
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "Public Reader" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "About" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Currently learning" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Rust/ }).getAttribute("href")).toBe("/topics/rust");
  expect(screen.getByRole("link", { name: "GitHub" }).getAttribute("rel")).toContain("noopener");
  expect(screen.queryByRole("link", { name: "Unsafe" })).toBeNull();
  expect(screen.getByRole("region", { name: "Reading activity for 2026" })).toBeTruthy();
  expect(screen.getByLabelText("2026-01-01: 3 article opens")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Create yours" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Copy Link" })).toBeNull();
  expect(screen.queryByLabelText("Markdown embed code")).toBeNull();
  expect(screen.queryByRole("img", { name: /Dev card for/ })).toBeNull();
  expect(screen.queryByRole("link", { name: "Edit profile" })).toBeNull();
});
it("offers owners an edit link without replacing the public profile data", () => {
  state.profile = { username: "reader" };
  render(<PublicUserProfile profile={profile} activity={null} />);
  expect(screen.getByRole("link", { name: "Edit profile" }).getAttribute("href")).toBe(
    "/settings/profile",
  );
  expect(screen.queryByRole("link", { name: "Create yours" })).toBeNull();
  expect(screen.getByText("Reading activity is currently unavailable.")).toBeTruthy();
});
it("omits empty optional sections without adding unsupported profile claims", () => {
  render(
    <PublicUserProfile
      profile={{ username: "reader", display_name: null, avatar_url: null }}
      activity={null}
    />,
  );
  expect(screen.getByRole("heading", { name: "reader" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Stack & technologies" })).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Profile links" })).toBeNull();
});
