// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PublicUserProfile } from "@/components/public-user-profile";
import type { UserProfile } from "@/lib/user";
const state = vi.hoisted(() => ({ profile: null as { username: string } | null }));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
afterEach(() => {
  cleanup();
  state.profile = null;
  vi.unstubAllGlobals();
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
      kind: "language",
      slug: "rust",
      logo_url: "https://cdn.example.org/rust.svg",
      status: "active",
      section: "learning",
      since_year: 2024,
    },
  ],
  reading_streak: { current_days: 3, longest_days: 8, total_days: 20, last_read_date: null },
};
let responseProfile: UserProfile;
let responseActivity: {
  year: number;
  timezone: string;
  days: { date: string; article_count: number }[];
} | null;
beforeEach(() => {
  responseProfile = profile;
  responseActivity = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ profile: responseProfile, activity: responseActivity })),
  );
});
it("shows the profile's identity, about, links, stack, and reading calendar", async () => {
  responseActivity = {
    year: 2026,
    timezone: "UTC",
    days: [
      { date: "2026-01-01", article_count: 3 },
      { date: "2026-01-02", article_count: 0 },
    ],
  };
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
  await screen.findByRole("heading", { name: "Public Reader" });
  expect(screen.getByRole("heading", { name: "Public Reader" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "About" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Currently learning" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Rust/ }).getAttribute("href")).toBe("/topics/rust");
  expect(screen.getByRole("link", { name: /Rust/ }).querySelector("img")?.getAttribute("src")).toBe(
    "https://cdn.example.org/rust.svg",
  );
  expect(screen.getByRole("link", { name: "GitHub" }).getAttribute("rel")).toContain("noopener");
  expect(screen.queryByRole("link", { name: "Unsafe" })).toBeNull();
  expect(screen.getByRole("region", { name: "Reading activity for 2026" })).toBeTruthy();
  expect(screen.getByText("2026", { exact: true }).parentElement?.textContent).toBe("2026");
  expect(screen.getByRole("img", { name: "2026-01-01: 3 article opens" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Create yours" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Copy Link" })).toBeNull();
  expect(screen.queryByLabelText("Markdown embed code")).toBeNull();
  expect(screen.queryByRole("img", { name: /Dev card for/ })).toBeNull();
  expect(screen.queryByRole("link", { name: "Edit profile" })).toBeNull();
});
it("offers owners an edit link without replacing the public profile data", async () => {
  state.profile = { username: "reader" };
  render(<PublicUserProfile profile={profile} activity={null} />);
  await screen.findByRole("link", { name: "Edit profile" });
  expect(screen.getByRole("link", { name: "Edit profile" }).getAttribute("href")).toBe(
    "/settings/profile",
  );
  expect(screen.queryByRole("link", { name: "Create yours" })).toBeNull();
  expect(screen.getByText("Reading activity is currently unavailable.")).toBeTruthy();
});
it("omits empty optional sections without adding unsupported profile claims", async () => {
  responseProfile = { username: "reader", display_name: null, avatar_url: null };
  render(
    <PublicUserProfile
      profile={{ username: "reader", display_name: null, avatar_url: null }}
      activity={null}
    />,
  );
  await screen.findByRole("heading", { name: "reader" });
  expect(screen.getByRole("heading", { name: "reader" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Stack & technologies" })).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Profile links" })).toBeNull();
});

it("hides cached profile details until visibility is reauthorized and fails closed", async () => {
  const description = document.createElement("meta");
  description.name = "description";
  const imageAlt = document.createElement("meta");
  imageAlt.name = "twitter:image:alt";
  document.head.append(description, imageAlt);
  render(<PublicUserProfile profile={profile} activity={null} />);
  await screen.findByText("Building useful things.");
  expect(description.content).toBe("Building useful things.");
  expect(imageAlt.content).toBe("Public Reader’s profile");
  let finish!: (response: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent(window, new PopStateEvent("popstate"));
  expect(screen.queryByText("Building useful things.")).toBeNull();
  expect(document.title).toBe("Profile unavailable · DevFeed");
  expect(description.content).toBe("This profile may be private or unavailable.");
  expect(imageAlt.content).toBe("Profile unavailable");
  description.remove();
  imageAlt.remove();
  await act(async () => finish(new Response(null, { status: 404 })));
  await screen.findByRole("heading", { name: "Profile unavailable" });
  expect(screen.queryByText("Building useful things.")).toBeNull();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/users/reader",
    expect.objectContaining({ credentials: "omit", cache: "no-store" }),
  );
});

it("aborts pre-freeze work and refreshes on a persisted pageshow", async () => {
  render(<PublicUserProfile profile={profile} activity={null} />);
  await screen.findByText("Building useful things.");
  fireEvent(window, new PageTransitionEvent("pagehide", { persisted: true }));
  expect(screen.queryByText("Building useful things.")).toBeNull();
  responseProfile = { ...profile, bio: "Updated while away" };
  fireEvent(window, new PageTransitionEvent("pageshow", { persisted: true }));
  await screen.findByText("Updated while away");
  expect(screen.queryByText("Building useful things.")).toBeNull();
});

it("reauthorizes new router props for the same username instead of retaining old details", async () => {
  const view = render(<PublicUserProfile profile={profile} activity={null} />);
  await screen.findByText("Building useful things.");
  responseProfile = { ...profile, bio: "Fresh public biography" };
  view.rerender(
    <PublicUserProfile profile={{ ...profile, bio: "Cached router biography" }} activity={null} />,
  );
  expect(screen.queryByText("Building useful things.")).toBeNull();
  expect(screen.queryByText("Cached router biography")).toBeNull();
  await screen.findByText("Fresh public biography");
});
