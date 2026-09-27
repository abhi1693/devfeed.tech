// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { cardThemes, cardAccents, cardThemeTokens } from "@devfeed/theme/dev-card";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DevCardXHeader } from "@/components/dev-card-x-header";
import { DevCardPreview } from "@/components/dev-card-preview";
import { devCardData } from "@/lib/dev-card";
import * as cardExport from "@/lib/dev-card";
import { extensionEvent } from "@/lib/extension-analytics";
import type { UserIdentity, UserProfile } from "@/lib/user";

const user: UserIdentity = {
  user_id: "private-id",
  name: "Maya Chen",
  email: "private@example.com",
  csrf_token: "secret",
  expires_at: 9999999999,
};
const profile: UserProfile = {
  display_name: "Maya Chen",
  avatar_url: null,
  username: "maya",
  bio: "Building useful things.",
  reading_streak: { current_days: 7, longest_days: 12, total_days: 50, last_read_date: null },
  stack: Array.from({ length: 7 }, (_, index) => ({
    topic_id: String(index),
    name: `Tool ${index}`,
    slug: `tool-${index}`,
    kind: "tool",
    section: "primary" as const,
    since_year: null,
    logo_url: null,
    status: "active" as const,
  })),
};
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const themeCss = readFileSync(`${import.meta.dirname}/../../../packages/theme/tokens.css`, "utf8");
const parseTokens = (css: string) =>
  Object.fromEntries(
    [...css.matchAll(/(--[\w-]+):\s*(#[\da-f]{6});/gi)].map((match) => [match[1], match[2]]),
  );
const lightTokens = parseTokens(themeCss.split(".dark")[0]);
const darkTokens = parseTokens(themeCss.split(".dark")[1]);
function luminance(hex: string) {
  const channels = hex
    .slice(1)
    .match(/../g)!
    .map((part) => parseInt(part, 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
it.each(
  ["light", "dark"].flatMap((mode) =>
    cardThemes.flatMap(({ id: theme }) =>
      cardAccents.map(({ id: accent }) => ({ mode, theme, accent })),
    ),
  ),
)("keeps text contrast above 4.5:1 for $theme / $accent / $mode", ({ mode, theme, accent }) => {
  const tokens = {
    ...lightTokens,
    ...(mode === "dark" ? darkTokens : {}),
    ...cardThemeTokens(theme, accent),
  };
  for (const [foreground, background] of [
    ["--card-foreground", "--card"],
    ["--muted-foreground", "--card"],
    ["--secondary-foreground", "--secondary"],
  ]) {
    const values = [luminance(tokens[foreground]), luminance(tokens[background])].sort(
      (a, b) => b - a,
    );
    expect((values[0] + 0.05) / (values[1] + 0.05)).toBeGreaterThanOrEqual(4.5);
  }
});

it.each(["classic", "terminal", "aurora", "minimal"] as const)(
  "renders a static %s cover with bounded content and no account data",
  (theme) => {
    const data = devCardData(
      {
        ...profile,
        dev_card: { theme, accent: "rose", motion: "animated", stats: ["longest_streak"] },
      },
      user,
    );
    const { container } = render(<DevCardXHeader data={data} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 1500 500");
    expect(container.querySelector("image[data-avatar]")).toBeNull();
    expect(container.textContent).not.toContain(data.initials);
    expect(svg.getAttribute("data-card-motion")).toBe("static");
    expect(svg.getAttribute("data-card-theme")).toBe(theme);
    expect(svg.getAttribute("aria-describedby")).toBe(container.querySelector("desc")?.id);
    expect(container.querySelector("desc")?.textContent).toContain("Building useful things.");
    expect(svg.getAttribute("data-card-accent")).toBe("rose");
    for (const node of svg.querySelectorAll("[fill], [stroke], [mask], [clip-path]")) {
      for (const attribute of ["fill", "stroke", "mask", "clip-path"]) {
        const reference = node.getAttribute(attribute)?.match(/^url\(#(.+)\)$/)?.[1];
        if (reference) expect(document.getElementById(reference)).not.toBeNull();
      }
    }
    expect(container.querySelectorAll("[data-header-technologies] > g")).toHaveLength(7);
    expect(container.textContent).not.toContain("+3");
    expect(container.querySelectorAll("[data-header-stats] > g")).toHaveLength(1);
    expect(container.textContent).toContain("Best streak");
    expect(container.textContent).not.toMatch(
      /Current streak|Days read|private@example|private-id|secret/,
    );
  },
);

it("honors empty selections without adding generic filler", () => {
  const data = devCardData(
    { ...profile, bio: "", dev_card: { technologies: [], stats: [] } },
    user,
  );
  const { container } = render(<DevCardXHeader data={data} />);
  expect(container.querySelector("[data-header-stats]")).toBeNull();
  expect(container.querySelector("[data-header-technologies]")).toBeNull();
  expect(container.querySelectorAll("[data-header-section]")).toHaveLength(2);
  expect(container.textContent).not.toMatch(/Tool|Reputation|Your bio|Add /);
});

it("exports the cover at native size and makes a failed export retryable", async () => {
  const exportImage = vi
    .spyOn(cardExport, "devCardPng")
    .mockRejectedValue(new Error("Canvas failed"));
  render(<DevCardPreview profile={profile} user={user} unsaved={false} />);
  fireEvent.click(screen.getByRole("radio", { name: "Header" }));
  fireEvent.click(screen.getByRole("button", { name: "Download X header" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Couldn’t create your image. Please try again.",
  );
  expect(exportImage).toHaveBeenCalledWith(expect.any(SVGSVGElement), 1);
  expect(exportImage.mock.calls[0][0].getAttribute("viewBox")).toBe("0 0 1500 500");
  expect(screen.getByRole("button", { name: "Download X header" })).toHaveProperty(
    "disabled",
    false,
  );
  expect(screen.queryByRole("button", { name: "Download card" })).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Dev Card" }));
  expect(screen.getByRole("button", { name: "Download card" })).toHaveProperty("disabled", false);
});

it("defaults to the card and preserves the selected preview while applying theme changes", () => {
  const { container, rerender } = render(
    <DevCardPreview profile={profile} user={user} unsaved={false} />,
  );
  expect(screen.getByRole("radio", { name: "Dev Card" }).getAttribute("aria-checked")).toBe("true");
  expect(container.querySelector(".dev-card-x-header")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Header" }));
  expect(container.querySelector(".dev-card-artwork")).toBeNull();
  rerender(
    <DevCardPreview
      profile={{
        ...profile,
        dev_card: { theme: "terminal", accent: "teal", stats: ["longest_streak"] },
      }}
      user={user}
      unsaved
    />,
  );
  expect(screen.getByRole("radio", { name: "Header" }).getAttribute("aria-checked")).toBe("true");
  expect(container.querySelector(".dev-card-x-header")?.getAttribute("data-card-theme")).toBe(
    "terminal",
  );
  expect(container.querySelector(".dev-card-x-header")?.getAttribute("data-card-accent")).toBe(
    "teal",
  );
  expect(screen.getByRole("button", { name: "Download X header" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("allows the header download event through the extension relay without profile fields", () => {
  expect(
    extensionEvent({ name: "dev_card_share", params: { method: "download_x_header" } }),
  ).toEqual({ name: "dev_card_share", params: { method: "download_x_header" } });
  expect(
    extensionEvent({
      name: "dev_card_share",
      params: { method: "download_x_header", name: "Maya" },
    }),
  ).toBeNull();
});

it("keeps every selected technology logo in the header", () => {
  const selected = profile.stack!.slice(0, 6).map((topic) => ({
    ...topic,
    logo_url: `https://example.com/${topic.topic_id}.png`,
  }));
  const { container } = render(
    <DevCardXHeader data={devCardData({ ...profile, stack: selected }, user)} />,
  );
  expect(container.querySelectorAll("image[data-technology-logo]")).toHaveLength(6);
  expect(container.querySelectorAll("[data-technology-fallback]")).toHaveLength(6);
  expect(container.querySelector("[data-header-technologies]")?.textContent).not.toContain("+2");
});
