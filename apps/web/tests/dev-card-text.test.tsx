// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import {
  cardTextLayout,
  fitCardText,
  fittingPrefix,
  measureCardText,
  wrapCardText,
} from "@/lib/dev-card-text";
import { DevCardArtwork } from "@/components/dev-card-artwork";
import { devCardData } from "@/lib/dev-card";
import { renderDevCardSvg } from "@/lib/server/dev-card-svg";
vi.mock("server-only", () => ({}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shares identical text, positions, sizes and height between preview and embed", async () => {
  for (const name of [
    "Abhimanyu Saharan",
    "W".repeat(100),
    "Zoë 👩🏽‍💻 José 李明",
    "e\u0301".repeat(50),
  ]) {
    const profile = {
      display_name: name,
      avatar_url: null,
      bio: "Building useful things. 👩🏽‍💻 界 ".repeat(5),
      reading_streak: { current_days: 2, longest_days: 1234, total_days: 42, last_read_date: null },
    };
    const { container, unmount } = render(<DevCardArtwork data={devCardData(profile, { name })} />);
    const svg = new DOMParser().parseFromString(
      await renderDevCardSvg(profile),
      "image/svg+xml",
    ).documentElement;
    const text = (root: Element) =>
      Array.from(root.querySelectorAll("text"), (node) => ({
        text: node.textContent,
        x: node.getAttribute("x"),
        y: node.getAttribute("y"),
        size: node.getAttribute("font-size"),
      }));
    expect(text(container)).toEqual(text(svg));
    expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe(
      svg.getAttribute("viewBox"),
    );
    expect(container.querySelector(".dev-card-stats")?.getAttribute("transform")).toBe(
      svg.querySelector(".dev-card-stats")?.getAttribute("transform"),
    );
    unmount();
  }
});

it("uses logarithmic prefix searches for long text", () => {
  const measure = vi.fn((value: string) => value.length);
  expect(fittingPrefix(Array.from("W".repeat(1024)), 37, measure, "…")).toBe(36);
  expect(measure.mock.calls.length).toBeLessThanOrEqual(11);
});

it("preserves graphemes and full bios, and bounds names to two lines", () => {
  const grapheme = "👩🏽‍💻";
  const lines = wrapCardText(grapheme.repeat(12), 40, (text) =>
    measureCardText(text, { size: 20 }),
  );
  expect(lines).toEqual(Array(12).fill(grapheme));
  const layout = cardTextLayout("W".repeat(100), "界".repeat(160));
  expect(layout.names).toHaveLength(2);
  expect(layout.names[1]).toMatch(/…$/);
  expect(layout.bios.join("")).toBe("界".repeat(160));
  expect(layout.bios.every((line) => measureCardText(line, { size: 17 }) <= 480)).toBe(true);
  expect(cardTextLayout("", "")).toEqual({ names: [], bios: [] });
});

it("caches font advances across sizes and bounds the cache", () => {
  const segment = vi.spyOn(Intl.Segmenter.prototype, "segment");
  measureCardText("Cache probe unique", { size: 20 });
  const calls = segment.mock.calls.length;
  measureCardText("Cache probe unique", { size: 40, spacing: -1 });
  expect(segment.mock.calls.length).toBe(calls);
  for (let i = 0; i < 1025; i++) measureCardText(`Cache probe ${i}`, { size: 20 });
  const evictedCalls = segment.mock.calls.length;
  measureCardText("Cache probe unique", { size: 20 });
  expect(segment.mock.calls.length).toBe(evictedCalls + 1);
});

it("fits at the minimum font size with ellipsis and accounts for letter spacing", () => {
  const font = { size: 44, weight: 800, spacing: -1.2 };
  const result = fitCardText("W".repeat(100), font, 100, 30);
  expect(result.size).toBe(30);
  expect(result.text).toMatch(/…$/);
  expect(result.width).toBeLessThanOrEqual(100);
  const scaled = fitCardText("Example name", { size: 44, spacing: 2 }, 100);
  expect(scaled.size).toBeLessThan(44);
  expect(scaled.text).toBe("Example name");
  expect(scaled.width).toBeLessThanOrEqual(100);
  expect(measureCardText("é", font)).toBe(measureCardText("e\u0301", font));
});
