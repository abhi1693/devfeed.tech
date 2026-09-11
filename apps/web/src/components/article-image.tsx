"use client";

/* eslint-disable @next/next/no-img-element -- Publisher images load directly, without a server-side proxy. */
import { useState } from "react";
import { Braces } from "lucide-react";

export function ArticleImage({ src, label }: { src?: string; label: string }) {
  const [failedSrc, setFailedSrc] = useState<string>();
  return src && failedSrc !== src ? (
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
    <span className="image-placeholder">
      <Braces size={42} strokeWidth={1} />
      <span>{label}</span>
    </span>
  );
}
