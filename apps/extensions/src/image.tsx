import type { ComponentProps } from "react";

// Publisher images use the website's ArticleImage directly. This adapter is
// only for Next's static brand asset, bundled with the extension.
export default function Image({
  priority,
  src,
  ...props
}: Omit<ComponentProps<"img">, "src"> & {
  src: string | { src: string };
  priority?: boolean;
}) {
  return (
    <img
      {...props}
      src={typeof src === "string" ? src : src.src}
      loading={priority ? "eager" : props.loading}
      fetchPriority={priority ? "high" : props.fetchPriority}
    />
  );
}
