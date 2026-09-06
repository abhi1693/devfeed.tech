"use client";

import { useState } from "react";
import { ImageIcon, ImageOff, LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/** Key this component by URL so late events cannot update a different preview. */
export function ImagePreview({ src, compact = false, className, loading }: { src: string | null; compact?: boolean; className?: string; loading?: "lazy" | "eager" }) {
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">("loading");
  const label = !src ? "No logo preview" : status === "failed" ? "Preview unavailable" : "Loading preview";
  const Icon = !src ? ImageIcon : status === "failed" ? ImageOff : LoaderCircle;
  return <div className={cn("relative flex items-center justify-center overflow-hidden", compact ? "size-5" : "h-48 max-h-[min(50vh,16rem)] w-full rounded bg-muted/40 sm:h-56", className)}>
    {(!src || status !== "loaded") && <div role="status" aria-label={label} title={label} className={cn("flex items-center gap-2 text-muted-foreground", !compact && "flex-col p-4 text-xs")}>
      <Icon aria-hidden className={cn("size-4 shrink-0", src && status === "loading" && "animate-spin motion-reduce:animate-none")} />
      {!compact && <span>{label}</span>}
    </div>}
    {src && status !== "failed" && <>
      {/* Arbitrary external URLs must load in the browser, not through Next's server-side image optimizer. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={compact ? "Logo preview" : "Image preview"} referrerPolicy="no-referrer" decoding="async" loading={loading}
        onLoad={() => setStatus("loaded")} onError={() => setStatus("failed")}
        className={cn("absolute inset-0 size-full object-contain", status !== "loaded" && "invisible")} />
    </>}
  </div>;
}
