"use client";

import { useEffect, useState } from "react";
import { UrlInput, type UrlInputProps } from "@/components/atoms/url-input";
import { imagePreviewUrl } from "@/lib/image-preview";
import { cn } from "@/lib/utils";
import { ImagePreview } from "./image-preview";

export function LogoUrlField({ value, className, disabled, ...props }: UrlInputProps) {
  const url = imagePreviewUrl(value);
  const [settledUrl, setSettledUrl] = useState(url);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledUrl(url), 300);
    return () => window.clearTimeout(timer);
  }, [url]);
  const preview = settledUrl === url ? url : null;
  return (
    <div className="relative">
      <UrlInput {...props} value={value} disabled={disabled} className={cn("pl-10", className)} />
      <div
        className={cn(
          "pointer-events-none absolute inset-y-0 left-3 flex items-center",
          disabled && "opacity-50",
        )}
      >
        <ImagePreview key={preview} src={preview} compact />
      </div>
    </div>
  );
}
