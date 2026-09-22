// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { cardLines, devCardData, wrapCardBio } from "@/lib/dev-card";
import * as cardExport from "@/lib/dev-card";
import { DevCardArtwork } from "@/components/dev-card-artwork";
import { DevCardTechnologyIcon } from "@/components/dev-card-technology-icon";
import { siDotnet, siKubernetes } from "simple-icons";
import { DevCardPreview } from "@/components/dev-card-preview";
import { ProfileSettings } from "@/components/profile-settings";
import { UserAccount, UserProvider } from "@/components/user-account";
import type { UserIdentity, UserProfile } from "@/lib/user";

const user: UserIdentity = {
  user_id: "private-id",
  name: "Alex Morgan",
  email: "private@example.org",
  csrf_token: "secret",
  expires_at: 9999999999,
};
const profile: UserProfile = {
  display_name: "Maya Chen",
  avatar_url: null,
  username: "mayacodes",
  bio: "Building useful things for the web.",
  location: "Berlin",
  reading_streak: {
    current_days: 8,
    longest_days: 24,
    total_days: 128,
    last_read_date: "2026-09-22",
  },
  stack: [
    {
      topic_id: "typescript",
      name: "TypeScript",
      section: "primary",
      since_year: 2020,
      slug: "typescript",
      logo_url: null,
      status: "active",
    },
  ],
  visibility: { public: false, location: false, stack: false, heatmap: false, achievements: false },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses shared theme tokens rather than a hard-coded card palette", () => {
  for (const path of ["components/dev-card-artwork.tsx", "app/styles/dev-card.css"]) {
    const source = readFileSync(`${import.meta.dirname}/../src/${path}`, "utf8");
    expect(source).not.toMatch(/#[\da-f]{3,8}\b|(?:rgb|hsl)a?\(/i);
  }
  const { container } = render(<DevCardArtwork data={devCardData(profile, user)} />);
  expect(container.querySelector('[stop-color="var(--chart-1)"]')).toBeTruthy();
  expect(container.querySelector('[fill="var(--card)"]')).toBeTruthy();
  expect(container.querySelector(".dev-card-brand image[data-brand-mark]")).toBeTruthy();
  expect(container.querySelectorAll(".dev-card-brand text")).toHaveLength(1);
  expect(container.textContent).not.toContain("</>");
  expect(container.querySelector(".dev-card-stats rect")).toBeNull();
});

it("always includes profile sections even with legacy hidden flags, but never account secrets", () => {
  const data = devCardData(profile, user);
  expect(data.name).toBe("Maya Chen");
  expect(data.initials).toBe("MC");
  expect(data.stats.map((stat) => stat.value)).toEqual([8, 24, 128]);
  expect(data.technologies).toEqual(["TypeScript"]);
  expect(data.location).toBe("Berlin");
  const content = JSON.stringify(data);
  for (const hidden of [user.email, user.user_id, user.csrf_token]) {
    expect(content).not.toContain(hidden);
  }
});

it("uses authentic bundled technology marks without inventing generic icons", () => {
  const { container } = render(
    <svg>
      <DevCardTechnologyIcon name=".NET" />
      <DevCardTechnologyIcon name="Kubernetes" />
      <DevCardTechnologyIcon name="AI Bots" />
      <DevCardTechnologyIcon name="Unknown technology" />
    </svg>,
  );
  const paths = Array.from(container.querySelectorAll('[data-technology-icon="brand"] path'));
  expect(paths.map((node) => node.getAttribute("d"))).toEqual([siDotnet.path, siKubernetes.path]);
  expect(container.querySelectorAll('[data-technology-icon="generic"]')).toHaveLength(0);
  expect(container.querySelectorAll('[data-technology-icon="brand"]')).toHaveLength(2);
  expect(container.querySelector("image")).toBeNull();
});

it("shows larger icon-only stack items and text-only fallbacks with accessible names", () => {
  const data = {
    ...devCardData(profile, user),
    technologies: [".NET", "Kubernetes", "AI Bots", "Custom tool"],
  };
  const { container } = render(<DevCardArtwork data={data} />);
  const items = Array.from(container.querySelectorAll(".dev-card-technologies > g"));
  expect(items.map((item) => item.getAttribute("aria-label"))).toEqual(data.technologies);
  for (const item of items.slice(0, 2)) {
    expect(item.querySelector("text")).toBeNull();
    expect(item.querySelector("svg")?.getAttribute("width")).toBe("36");
    expect(item.querySelector("rect")?.getAttribute("height")).toBe("64");
  }
  for (const [index, item] of items.slice(2).entries()) {
    expect(item.querySelector("svg")).toBeNull();
    expect(item.querySelector("text")?.textContent).toBe(data.technologies[index + 2]);
    expect(item.querySelector("text")?.getAttribute("font-size")).toBe("16");
  }
});

it("uses a decorative grid and contracts the canvas for sparse profiles", () => {
  const { container, rerender } = render(<DevCardArtwork data={devCardData(profile, user)} />);
  const fullHeight = Number(container.querySelector(".dev-card-artwork")?.getAttribute("height"));
  const grid = container.querySelector(".dev-card-grid")?.outerHTML;
  expect(container.querySelector(".dev-card-grid")?.getAttribute("aria-hidden")).toBe("true");
  rerender(
    <DevCardArtwork data={devCardData({ display_name: "Maya Chen", avatar_url: null }, user)} />,
  );
  expect(container.querySelector(".dev-card-grid")?.outerHTML).toBe(grid);
  expect(Number(container.querySelector(".dev-card-artwork")?.getAttribute("height"))).toBeLessThan(
    fullHeight,
  );
});

it("uses actual reading days and keeps new users shareable", () => {
  const data = devCardData(
    {
      ...profile,
      visibility: { ...profile.visibility!, heatmap: true, stack: true, location: true },
    },
    user,
  );
  expect(data.stats).toEqual([
    { label: "DAY STREAK", value: 8 },
    { label: "BEST STREAK", value: 24 },
    { label: "DAYS READING", value: 128 },
  ]);
  expect(data.technologies).toEqual(["TypeScript"]);
  expect(data.location).toBe("Berlin");
  const empty = devCardData(
    { display_name: null, avatar_url: "javascript:alert(1)" },
    { ...user, name: null },
  );
  expect(empty.name).toBe("DevFeed reader");
  expect(empty.avatar).toBeNull();
  expect(empty.stats).toEqual([]);
  expect(devCardData(profile, user)).toEqual(
    devCardData(profile, { ...user, user_id: "different" }),
  );
});

it("wraps long and non-Latin text within bounded lines", () => {
  expect(cardLines("", 10, 2)).toEqual([]);
  expect(cardLines("hello world", 10, 2)).toEqual(["hello", "world"]);
  const lines = cardLines("界".repeat(100), 22, 2);
  expect(lines).toHaveLength(2);
  expect(lines[1].endsWith("…")).toBe(true);
  expect(lines.every((line) => Array.from(line).length <= 22)).toBe(true);
});

it("wraps every bio character, including long unbroken and non-Latin text", () => {
  for (const bio of [
    "Building useful things. ".repeat(7).slice(0, 160),
    "W".repeat(160),
    "界".repeat(160),
  ]) {
    const lines = wrapCardBio(bio, 40, (text) => Array.from(text).length);
    expect(lines.join("").replace(/\s/gu, "")).toBe(bio.replace(/\s/gu, ""));
    expect(lines.every((line) => Array.from(line).length <= 40)).toBe(true);
    expect(lines.length).toBeGreaterThan(2);
  }
  expect(wrapCardBio("", 40, (text) => text.length)).toEqual([]);
});

it("keeps badges free of generic slogans and duplicate branding", () => {
  const { container } = render(
    <DevCardArtwork data={devCardData({ display_name: "Alex Morgan", avatar_url: null }, user)} />,
  );
  const visibleText = Array.from(container.querySelectorAll("text"))
    .map((node) => node.textContent)
    .join(" ");
  expect(visibleText).toContain("devfeed.");
  for (const filler of [
    "DEV CARD",
    "DevFeed community",
    "Good ideas start with curiosity.",
    "Never done learning.",
    "Read. Build. Repeat.",
    "devfeed.tech",
    "The developer community.",
  ]) {
    expect(visibleText).not.toContain(filler);
  }
  expect(devCardData({ display_name: "Alex Morgan", avatar_url: null }, user).bio).toBe("");
});

it("renders a real accessible card, escaping user markup and never displaying reputation", () => {
  const { container } = render(
    <DevCardArtwork
      data={devCardData({ ...profile, display_name: "<script>alert(1)</script>" }, user)}
    />,
  );
  expect(screen.getByRole("img", { name: /Dev card for/ })).toBeTruthy();
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).not.toMatch(/reputation|posts read|private@example.org/i);
  expect(container.textContent).toContain("DAYS READING");
});

it("shows a retry state when profile loading fails, not a fabricated card", async () => {
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(url.endsWith("/me") ? Response.json(user) : Response.json({}, { status: 503 })),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <UserAccount />
      <ProfileSettings />
    </UserProvider>,
  );
  await screen.findByText("Couldn’t load your profile");
  expect(screen.queryByRole("button", { name: "Download card" })).toBeNull();
  const before = fetcher.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(fetcher.mock.calls.length).toBeGreaterThan(before);
});

it("prevents exporting unsaved profile drafts", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith("/me") ? user : profile))),
  );
  render(
    <UserProvider>
      <DevCardPreview profile={profile} user={user} unsaved />
    </UserProvider>,
  );
  expect(await screen.findByRole("button", { name: "Download card" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByText("Unsaved preview. Save your profile before sharing.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Your Dev Card" })).toBeTruthy();
  expect(screen.queryByText("Ready to share")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Open dev card preview" })).toBeNull();
});

it("links the avatar menu to settings and reports inline export failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith("/me") ? user : profile))),
  );
  vi.spyOn(cardExport, "devCardPng").mockRejectedValue(new Error("Canvas unavailable"));
  render(
    <UserProvider>
      <UserAccount />
      <DevCardPreview profile={profile} user={user} unsaved={false} />
    </UserProvider>,
  );
  const menu = await screen.findByRole("button", { name: "User menu: Maya Chen" });
  fireEvent.keyDown(menu, { key: "ArrowDown" });
  const cardLink = await screen.findByRole("menuitem", { name: "Dev card" });
  expect(cardLink.getAttribute("href")).toBe("/settings/profile");
  fireEvent.keyDown(cardLink, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Download card" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Couldn’t create your image. Please try again.",
  );
  expect(screen.getByRole("button", { name: "Download card" })).toHaveProperty("disabled", false);
});
