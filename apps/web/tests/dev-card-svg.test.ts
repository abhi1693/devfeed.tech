import { expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { renderDevCardSvg } from "@/lib/server/dev-card-svg";
import * as avatar from "@/lib/server/card-avatar";

it("includes the downloaded avatar as embedded image data", async () => {
  const image = "data:image/png;base64,iVBORw0KGgo=";
  const loader = vi.spyOn(avatar, "cardAvatar").mockResolvedValue(image);
  try {
    const svg = await renderDevCardSvg({
      display_name: "Reader",
      avatar_url: "https://example.com/avatar.png",
    });
    expect(loader).toHaveBeenCalledWith("https://example.com/avatar.png");
    expect(svg).toContain(`data-avatar="" href="${image}"`);
    expect(svg).not.toContain("https://example.com/avatar.png");
  } finally {
    loader.mockRestore();
  }
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
  expect(svg).toContain("data-brand-mark");
  expect(svg).toContain("data:image/png;base64,");
  expect(svg).toContain("&lt;script&gt;");
  expect(svg).toContain("day streak: 7.");
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
