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

it("bounds GitHub fallback avatars while preserving version queries and managed variants", () => {
  const original = "https://avatars.githubusercontent.com/u/5083532?v=4";
  expect(avatarSource(original, [], 30)).toBe(`${original}&s=30`);
  expect(avatarSource(original, [], 256)).toBe(`${original}&s=256`);
  expect(avatarSrcSet([], original)).toContain(`${original}&s=128 128w`);
  expect(avatarSrcSet([], `${original}&s=32`)).toContain(`${original}&s=128 128w`);
  expect(avatarSrcSet([], "javascript:bad")).toBeUndefined();
  expect(avatarSrcSet([], "https://publisher.test/avatar.png")).toBeUndefined();
  expect(avatarSource(original, [], 1000)).toBe(`${original}&s=512`);
  const managed: AvatarVariant[] = [{ url: "https://images.devfeed.tech/avatar.webp", width: 256 }];
  expect(avatarSource(original, managed, 30)).toBe(managed[0].url);
  for (const url of [
    "https://avatars.githubusercontent.com.evil.test/u/5083532?v=4",
    "https://publisher.test/avatar.png?signature=123",
    "https://avatars.githubusercontent.com/custom/avatar.png",
  ])
    expect(avatarSource(url, [], 30)).toBe(url);
});
