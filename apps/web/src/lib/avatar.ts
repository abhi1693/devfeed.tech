import { safeExternalUrl } from "./feed-query";
import type { AvatarVariant } from "./user";

function githubAvatar(source: string) {
  const avatar = new URL(source);
  return avatar.origin === "https://avatars.githubusercontent.com" &&
    /^\/u\/\d+$/.test(avatar.pathname)
    ? avatar
    : undefined;
}

export function avatarSource(
  url: string | null | undefined,
  variants: AvatarVariant[] = [],
  size = 256,
) {
  const valid = variants
    .filter((variant) => safeExternalUrl(variant.url))
    .sort((a, b) => a.width - b.width);
  const managed = (valid.find((variant) => variant.width >= size) ?? valid.at(-1))?.url;
  if (managed) return safeExternalUrl(managed);
  const source = safeExternalUrl(url);
  if (!source) return source;
  const avatar = githubAvatar(source);
  // GitHub's avatar CDN supports bounded sizes; preserve other publishers' URLs.
  if (avatar) {
    avatar.searchParams.set("s", String(Math.min(512, Math.max(1, Math.ceil(size)))));
    return avatar.href;
  }
  return source;
}

export function avatarSrcSet(variants: AvatarVariant[] = [], fallbackUrl?: string | null) {
  if (!variants.length && fallbackUrl) {
    const source = safeExternalUrl(fallbackUrl);
    if (source && githubAvatar(source))
      return [32, 64, 128, 256, 512]
        .map((width) => `${avatarSource(source, [], width)} ${width}w`)
        .join(", ");
  }
  return (
    variants
      .filter((variant) => safeExternalUrl(variant.url))
      .map((variant) => `${variant.url} ${variant.width}w`)
      .join(", ") || undefined
  );
}
