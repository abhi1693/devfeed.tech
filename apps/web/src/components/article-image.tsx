"use client";

/* eslint-disable @next/next/no-img-element -- Publisher images load directly, without a server-side proxy. */
import { useState } from "react";
import { Braces } from "lucide-react";
import { articleImageSources } from "@/lib/article-images";

export function ArticleImage({
  src,
  label,
  priority = false,
  sizes = "(max-width: 520px) calc(100vw - 40px), (max-width: 800px) calc((100vw - 62px) / 2), (max-width: 1200px) calc((100vw - 268px) / 2), (max-width: 1699px) calc((100vw - 326px) / 3), 430px",
}: {
  src?: string;
  label: string;
  priority?: boolean;
  sizes?: string;
}) {
  const [failedSrc, setFailedSrc] = useState<string>();
  const image = articleImageSources(src);
  return src && failedSrc !== src ? (
    <img
      ref={(image) => {
        // A cached failure can arrive before React attaches onError.
        if (image?.complete && image.naturalWidth === 0) setFailedSrc(src);
      }}
      src={image.src}
      srcSet={image.srcSet}
      sizes={image.srcSet ? sizes : undefined}
      alt=""
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : "auto"}
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
