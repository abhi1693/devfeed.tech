import { safeExternalUrl } from "./feed-query";
import type { AvatarVariant } from "./user";

export function avatarSource(
  url: string | null | undefined,
  variants: AvatarVariant[] = [],
  size = 256,
) {
  const valid = variants
    .filter((variant) => safeExternalUrl(variant.url))
    .sort((a, b) => a.width - b.width);
  return safeExternalUrl(
    (valid.find((variant) => variant.width >= size) ?? valid.at(-1))?.url ?? url,
  );
}

export function avatarSrcSet(variants: AvatarVariant[] = []) {
  return (
    variants
      .filter((variant) => safeExternalUrl(variant.url))
      .map((variant) => `${variant.url} ${variant.width}w`)
      .join(", ") || undefined
  );
}
