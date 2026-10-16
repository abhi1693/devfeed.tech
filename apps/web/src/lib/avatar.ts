import { readerImageUrl } from "./reader-runtime";
import { safeExternalUrl } from "./feed-query";
import type { AvatarVariant } from "./user";

export const githubAvatarWidths = [32, 64, 96, 128, 192, 256, 512] as const;

function githubAvatar(source: string) {
  const avatar = new URL(source);
  return avatar.origin === "https://avatars.githubusercontent.com" &&
    /^\/u\/\d{1,15}$/.test(avatar.pathname) &&
    !avatar.hash &&
    [...avatar.searchParams].every(
      ([key, value]) => ["s", "v"].includes(key) && /^\d{1,10}$/.test(value),
    )
    ? avatar
    : undefined;
}

export function avatarSource(
  url: string | null | undefined,
  variants: AvatarVariant[] = [],
  size = 256,
) {
  const valid = variants.filter(validAvatarVariant).sort((a, b) => a.width - b.width);
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
  const valid = variants.filter(validAvatarVariant);
  if (!valid.length && fallbackUrl) {
    const source = safeExternalUrl(fallbackUrl);
    if (source && githubAvatar(source))
      return [32, 64, 128, 256, 512]
        .map((width) => `${avatarSource(source, [], width)} ${width}w`)
        .join(", ");
  }
  return valid.map((variant) => `${variant.url} ${variant.width}w`).join(", ") || undefined;
}

function validAvatarVariant(variant: AvatarVariant) {
  return safeExternalUrl(variant.url) && Number.isInteger(variant.width) && variant.width > 0;
}

/** Only a fixed public GitHub URL pattern is optimized; other selected URLs stay intact. */
export function profileAvatarSources(
  url: string | null | undefined,
  variants: AvatarVariant[] = [],
  size = 30,
) {
  const source = safeExternalUrl(url);
  const github = source && !variants.some(validAvatarVariant) ? githubAvatar(source) : undefined;
  if (!github)
    return { src: avatarSource(url, variants, size), srcSet: avatarSrcSet(variants, url) };
  const id = github.pathname.split("/").at(-1)!;
  const version = github.searchParams.get("v");
  const resized = (width: number) =>
    readerImageUrl(`/api/avatars/github/${id}/${width}${version ? `?v=${version}` : ""}`);
  const width = githubAvatarWidths.find((width) => width >= size) ?? 512;
  return {
    src: resized(width),
    srcSet: githubAvatarWidths.map((width) => `${resized(width)} ${width}w`).join(", "),
  };
}
