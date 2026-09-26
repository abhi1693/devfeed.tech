"use client";

/* eslint-disable @next/next/no-img-element -- Catalog logos load directly without a server-side proxy. */
import { useState } from "react";
import {
  Blocks,
  Code2,
  Cpu,
  Database,
  Hash,
  Library,
  Monitor,
  Package,
  Rss,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { safeExternalUrl } from "@/lib/feed-query";

const topicFallbackIcons: Record<string, LucideIcon> = {
  database: Database,
  framework: Blocks,
  game_engine: Blocks,
  hardware: Cpu,
  language: Code2,
  library: Library,
  operating_system: Monitor,
  package: Package,
  runtime: Cpu,
  tool: Wrench,
};

export function CatalogIcon({
  url,
  source = false,
  kind,
  iconSize = 23,
}: {
  url: string | null;
  source?: boolean;
  kind?: string;
  iconSize?: number;
}) {
  const src = safeExternalUrl(url);
  const [failedSrc, setFailedSrc] = useState<string>();
  const hasLogo = src && failedSrc !== src;
  const FallbackIcon = source ? Rss : (topicFallbackIcons[kind ?? ""] ?? Hash);
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
      ) : (
        <FallbackIcon size={iconSize} strokeWidth={1.8} />
      )}
    </span>
  );
}
