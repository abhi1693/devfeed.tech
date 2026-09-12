"use client";

/* eslint-disable @next/next/no-img-element -- Catalog logos load directly without a server-side proxy. */
import { useState } from "react";
import { Hash, Rss } from "lucide-react";
import { safeExternalUrl } from "@/lib/feed-query";

export function CatalogIcon({ url, source = false }: { url: string | null; source?: boolean }) {
  const src = safeExternalUrl(url);
  const [failedSrc, setFailedSrc] = useState<string>();
  const hasLogo = src && failedSrc !== src;
  return (
    <span className={`topic-icon${hasLogo ? " topic-logo" : ""}`} aria-hidden="true">
      {hasLogo ? (
        <img
          ref={(image) => {
            // A cached failure can arrive before React attaches onError.
            if (image?.complete && image.naturalWidth === 0) setFailedSrc(src);
          }}
          src={src}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailedSrc(src)}
        />
      ) : source ? (
        <Rss size={23} />
      ) : (
        <Hash size={23} />
      )}
    </span>
  );
}
