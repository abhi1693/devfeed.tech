"use client";
/* eslint-disable @next/next/no-img-element -- User-selected avatars load directly, without a server proxy. */
import { useState } from "react";
import { UserRound } from "lucide-react";
import { safeExternalUrl } from "@/lib/feed-query";

export function ProfileAvatar({
  name,
  url,
}: {
  name?: string | null;
  url?: string | null;
}) {
  const [failed, setFailed] = useState<string>();
  const src = safeExternalUrl(url);
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
