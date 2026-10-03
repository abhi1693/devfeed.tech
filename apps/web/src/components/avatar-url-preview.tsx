"use client";
import { useState } from "react";
import { ImageIcon, ImageOff, LoaderCircle } from "lucide-react";
import { useDebouncedImagePreview } from "@devfeed/ui/use-debounced-image-preview";
import { safeExternalUrl } from "@/lib/feed-query";
import { avatarSource } from "@/lib/avatar";
import type { AvatarVariant } from "@/lib/user";
export function AvatarUrlPreview({
  value,
  variants,
}: {
  value: string | null;
  variants?: AvatarVariant[];
}) {
  const url = safeExternalUrl(avatarSource(value, variants, 32)) ?? null;
  const preview = useDebouncedImagePreview(url);
  return <AvatarPreview key={preview} src={preview} />;
}

function AvatarPreview({ src }: { src: string | null }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">("loading");
  const Icon = !src ? ImageIcon : status === "failed" ? ImageOff : LoaderCircle;
  const label = !src
    ? "No avatar preview"
    : status === "failed"
      ? "Preview unavailable"
      : "Loading preview";
  return (
    <span className="settings-avatar-preview">
      {(!src || status !== "loaded") && (
        <span role="status" aria-label={label} title={label}>
          <Icon
            size={16}
            aria-hidden="true"
            className={src && status === "loading" ? "settings-spinner" : undefined}
          />
        </span>
      )}
      {src && status !== "failed" && (
        // External images load directly in the browser, as in admin LogoUrlField.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt="Avatar preview"
          referrerPolicy="no-referrer"
          decoding="async"
          ref={(image) => {
            if (image?.complete) setStatus(image.naturalWidth > 0 ? "loaded" : "failed");
          }}
          onLoad={() => setStatus("loaded")}
          onError={() => setStatus("failed")}
          style={{ visibility: status === "loaded" ? "visible" : "hidden" }}
        />
      )}
    </span>
  );
}
