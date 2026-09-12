import { ExternalLink } from "lucide-react";
import { imagePreviewUrl } from "@/lib/image-preview";
import { cn } from "@/lib/utils";
import { ImagePreview } from "./image-preview";

/** Read-only media value; form URL controls keep their existing editing behavior. */
export function ImagePreviewLink({
  value,
  kind = "image",
  variant = "field",
}: {
  value: unknown;
  kind?: "logo" | "image";
  variant?: "field" | "heading";
}) {
  if (value === null || value === undefined || value === "")
    return <span className="text-muted-foreground">—</span>;
  const url = imagePreviewUrl(value);
  if (!url) return <span className="text-xs text-muted-foreground">Preview unavailable</span>;
  const label = `Open ${kind} in a new tab`;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      aria-label={label}
      title={label}
      className={cn(
        "group relative block w-fit max-w-full rounded-md border bg-muted/20 transition-colors hover:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
        variant === "heading" ? "p-1" : "p-2",
      )}
    >
      <ImagePreview
        key={url}
        src={url}
        compact={kind === "logo" || variant === "heading"}
        loading={variant === "heading" ? "eager" : "lazy"}
        className={
          variant === "heading"
            ? "size-8"
            : kind === "logo"
              ? "size-12"
              : "h-40 w-72 max-w-full sm:h-40"
        }
      />
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute right-1 top-1 rounded-sm bg-background/90 p-0.5 text-muted-foreground group-hover:text-foreground group-focus-visible:text-foreground",
          variant === "heading" &&
            "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100",
        )}
      >
        <ExternalLink className="size-3" />
      </span>
    </a>
  );
}
