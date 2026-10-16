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

it("optimizes only supported public GitHub avatar patterns and keeps selected managed avatars", async () => {
  const { profileAvatarSources } = await import("@/lib/avatar");
  const original = "https://avatars.githubusercontent.com/u/5083532?v=4";
  const optimized = profileAvatarSources(original, [], 104);
  expect(optimized.src).toBe("/api/avatars/github/5083532/128?v=4");
  expect(optimized.srcSet).toContain("/api/avatars/github/5083532/192?v=4 192w");
  const managed = [
    { width: 128, url: "https://images.test/avatar/128.webp?v=new" },
  ] as AvatarVariant[];
  expect(profileAvatarSources(original, managed, 104).src).toBe(managed[0].url);
  for (const url of [
    "https://avatars.githubusercontent.com.evil.test/u/5083532?v=4",
    "https://avatars.githubusercontent.com/u/5083532?signature=secret",
    "https://avatars.githubusercontent.com/u/5083532?v=../private",
    "https://publisher.test/custom.png?signature=123",
  ])
    expect(profileAvatarSources(url).src).toBe(url);
  expect(profileAvatarSources("javascript:bad").src).toBeUndefined();
  expect(
    profileAvatarSources(original, [{ url: "javascript:bad", width: 128 }] as AvatarVariant[]).src,
  ).toContain("/api/avatars/github/");
});
