"use client";
/* eslint-disable @next/next/no-img-element -- Managed and supported GitHub avatars use bounded optimized variants. */
import { useState } from "react";
import { UserRound } from "lucide-react";
import { profileAvatarSources } from "@/lib/avatar";
import type { AvatarVariant } from "@/lib/user";

export function ProfileAvatar({
  name,
  url,
  variants,
  size = 30,
  sizes,
}: {
  name?: string | null;
  url?: string | null;
  variants?: AvatarVariant[];
  size?: number;
  sizes?: string;
}) {
  const [failed, setFailed] = useState<string>();
  const { src, srcSet } = profileAvatarSources(url, variants, size);
  const words = name?.trim().split(/\s+/).filter(Boolean) ?? [];
  const initials = [words[0], ...(words.length > 1 ? [words.at(-1)] : [])]
    .filter(Boolean)
    .map((word) => Array.from(word!)[0])
    .join("")
    .toUpperCase();
  return (
    <span className="profile-avatar" aria-hidden="true">
      {src && failed !== src ? (
        <img
          src={src}
          srcSet={srcSet}
          sizes={sizes ?? `${size}px`}
          alt=""
          referrerPolicy="no-referrer"
          ref={(image) => {
            if (image?.complete && image.naturalWidth === 0) setFailed(src);
          }}
          onError={() => setFailed(src)}
        />
      ) : (
        initials || <UserRound size={18} />
      )}
    </span>
  );
}
