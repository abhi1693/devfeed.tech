import { expect, it } from "vitest";
import { avatarSource, avatarSrcSet } from "@/lib/avatar";
import type { AvatarVariant } from "@/lib/user";

it("selects small and profile-sized variants and preserves legacy URLs", () => {
  const variants = [32, 128, 256].map((width) => ({
    width,
    url: `https://images.test/${width}.webp`,
  })) as AvatarVariant[];
  expect(avatarSource("https://example.com/original", variants, 30)).toBe(variants[0].url);
  expect(avatarSource("https://example.com/original", variants, 116)).toBe(variants[1].url);
  expect(avatarSource("https://example.com/original", variants, 512)).toBe(variants[2].url);
  expect(avatarSource("https://example.com/original")).toBe("https://example.com/original");
  expect(avatarSource("javascript:bad")).toBeUndefined();
  expect(avatarSrcSet(variants)).toContain(`${variants[2].url} 256w`);
});
