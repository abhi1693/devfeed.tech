import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { renderDevCardSvg } from "@/lib/server/dev-card-svg";
import * as avatar from "@/lib/server/card-avatar";
import * as logos from "@/lib/server/card-logos";
import { classicCardDots } from "@devfeed/theme/dev-card-motion";

afterEach(() => vi.restoreAllMocks());

it("embeds only the selected stat's matching icon as self-contained SVG", async () => {
  const svg = await renderDevCardSvg({
    display_name: "Reading fan",
    avatar_url: null,
    reading_streak: { current_days: 2, longest_days: 10, total_days: 30, last_read_date: null },
    dev_card: { stats: ["total_reading_days"] },
  });
  expect(svg).toContain('data-stat-icon="total_reading_days"');
  expect(svg).toContain("lucide-calendar-days");
  expect(svg).not.toContain('data-stat-icon="current_streak"');
  expect(svg).not.toContain('data-stat-icon="longest_streak"');
  expect(svg).not.toMatch(/stroke="var\(/);
});

it("scatters Classic dots with stable, independent animation timing", async () => {
  expect(classicCardDots).toHaveLength(275);
  expect(new Set(classicCardDots.map((dot) => dot.duration)).size).toBeGreaterThan(200);
  expect(new Set(classicCardDots.map((dot) => dot.delay)).size).toBeGreaterThan(200);
  expect(new Set(classicCardDots.map((dot) => dot.color)).size).toBe(3);
  const profile = {
    display_name: "Dot reader",
    avatar_url: null,
    dev_card: { theme: "classic" as const, motion: "animated" as const, stats: [] },
  };
  const svg = await renderDevCardSvg(profile);
  expect(svg).toBe(await renderDevCardSvg(profile));
  expect(svg.match(/class="dev-card-dot"/g)).toHaveLength(275);
  expect(svg).toContain("@keyframes devfeed-card-dot");
  expect(svg).not.toContain("dev-card-tile-glow");
});

it("embeds stack logos and uses readable fallbacks without any external image references", async () => {
  const image = "data:image/png;base64,iVBORw0KGgo=";
  vi.spyOn(logos, "cachedCardLogos").mockReturnValue(
    new Map([
      ["https://example.com/logo.svg", image],
      ["https://example.com/missing.png", null],
    ]),
  );
  const warm = vi.spyOn(logos, "warmCardLogos").mockResolvedValue();
  const svg = await renderDevCardSvg({
    display_name: "Reader",
    avatar_url: null,
    stack: [
      {
        topic_id: "k8s",
        name: "Kubernetes",
        slug: "kubernetes",
        kind: "platform",
        section: "primary",
        status: "active",
        since_year: null,
        logo_url: "https://example.com/logo.svg",
      },
      {
        topic_id: "missing",
        name: "Unavailable",
        slug: "unavailable",
        kind: "tool",
        section: "primary",
        status: "active",
        since_year: null,
        logo_url: "https://example.com/missing.png",
      },
    ],
  });
  const logo = svg.match(/<image[^>]*data-technology-logo="k8s"[^>]*>/)?.[0];
  expect(logo).toContain(`href="${image}"`);
  expect(svg).toMatch(/visibility="visible" data-technology-fallback="missing"/);
  expect(svg).not.toContain('data-technology-logo="missing"');
  expect(svg).not.toMatch(/href="https?:/);
  expect(warm).toHaveBeenCalled();
});

it("includes a cached avatar and warms uncached profile images without delaying the SVG", async () => {
  const image = "data:image/png;base64,iVBORw0KGgo=";
  const loader = vi.spyOn(avatar, "cachedCardAvatar").mockReturnValue(image);
  const warm = vi.spyOn(avatar, "warmCardAvatar").mockResolvedValue(image);
  try {
    const svg = await renderDevCardSvg({
      display_name: "Reader",
      avatar_url: "https://example.com/avatar.png",
    });
    expect(loader).toHaveBeenCalledWith("https://example.com/avatar.png");
    expect(warm).not.toHaveBeenCalled();
    expect(svg).toContain(`data-avatar="" href="${image}"`);
    expect(svg).not.toContain("https://example.com/avatar.png");
  } finally {
    loader.mockRestore();
    warm.mockRestore();
  }
});

it("renders a fast fallback while warming uncached images", async () => {
  vi.spyOn(avatar, "cachedCardAvatar").mockReturnValue(null);
  const warmAvatar = vi.spyOn(avatar, "warmCardAvatar").mockResolvedValue(null);
  vi.spyOn(logos, "cachedCardLogos").mockReturnValue(new Map());
  const warmLogos = vi.spyOn(logos, "warmCardLogos").mockResolvedValue();
  const svg = await renderDevCardSvg({
    display_name: "Reader",
    avatar_url: "https://example.com/avatar.png",
    stack: [
      {
        topic_id: "k8s",
        name: "Kubernetes",
        slug: "kubernetes",
        kind: "platform",
        section: "primary",
        status: "active",
        since_year: null,
        logo_url: "https://example.com/logo.svg",
      },
    ],
  });
  expect(svg).not.toContain("https://example.com/");
  expect(svg).not.toContain('data-avatar=""');
  expect(svg).toContain('data-technology-fallback="k8s"');
  expect(warmAvatar).toHaveBeenCalledWith("https://example.com/avatar.png");
  expect(warmLogos).toHaveBeenCalled();
});
it("renders only the shared card artwork with escaped content and embedded local assets", async () => {
  const svg = await renderDevCardSvg({
    display_name: '<script>alert("name")</script>',
    avatar_url: "http://127.0.0.1/private-avatar",
    username: "reader",
    bio: "Build & learn <every day> ".repeat(6),
    reading_streak: { current_days: 7, longest_days: 12, total_days: 30, last_read_date: null },
  });
  expect(svg.startsWith("<svg")).toBe(true);
  expect(svg).toContain('class="dev-card-grid"');
  expect(svg).toContain('class="dev-card-brand"');
  expect(svg).toContain("devfeed.");
  expect(svg).toContain("data-brand-mark");
  expect(svg).toContain("data:image/png;base64,");
  expect(svg).toContain("&lt;script&gt;");
  expect(svg).toContain("current streak: 7.");
  expect(svg).not.toContain("<script>");
  expect(svg).not.toContain("<html");
  expect(svg).not.toContain("127.0.0.1");
  expect(svg).not.toContain("var(--");
  expect(svg).toContain('fill="#ffffff"');
});

it("does not rewrite CSS variable examples in a user's bio", async () => {
  const svg = await renderDevCardSvg({
    display_name: "Reader",
    avatar_url: null,
    bio: "I use var(--my-color) in CSS.",
  });
  expect(svg).toContain("var(--my-color)");
});

it("exports every saved design with resolved colors and no external dependencies", async () => {
  for (const theme of ["terminal", "aurora", "minimal"] as const) {
    const svg = await renderDevCardSvg({
      display_name: "Theme reader",
      avatar_url: null,
      dev_card: { theme, accent: "rose", stats: [], technologies: [] },
    });
    expect(svg).toContain(`data-card-theme="${theme}"`);
    expect(svg).toContain('data-card-accent="rose"');
    expect(svg).toContain('stop-color="#f08bb5"');
    expect(svg).not.toMatch(/(?:fill|stroke|stop-color|font-family)="var\(/);
    expect(svg).not.toMatch(/href="https?:/);
  }
});

it("keeps static embeds still and packages animation with reduced-motion support", async () => {
  for (const motion of ["static", "animated"] as const) {
    const svg = await renderDevCardSvg({
      display_name: "Motion reader",
      avatar_url: null,
      dev_card: { theme: "aurora", motion, stats: [] },
    });
    expect(svg).toContain(`data-card-motion="${motion}"`);
    expect(svg.includes("data-card-motion-style")).toBe(motion === "animated");
    if (motion === "animated") {
      expect(svg).toContain("prefers-reduced-motion: reduce");
      expect(svg).toContain("@keyframes devfeed-card-drift");
    }
    expect(svg).not.toMatch(/<script|href="https?:/);
  }
});
