import type { ComponentProps } from "react";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "./input";

export type UrlInputProps = Omit<ComponentProps<typeof Input>, "type"> & {
  loading?: boolean;
  loadingText?: string;
};

/** Shared native URL validation and editing behavior for URL-based fields. */
export function UrlInput({ loading, loadingText = "Checking URL…", className, ...props }: UrlInputProps) {
  // Keep the input mounted and reserve icon space while a lookup starts/stops.
  // Existing URL controls without a loading state retain their current structure.
  const input = <Input spellCheck={false} autoCapitalize="none" {...props} type="url"
    aria-busy={loading ?? props["aria-busy"]} className={cn(className, loading !== undefined && "pr-10")} />;
  if (loading === undefined) return input;
  return <div className="relative min-w-0">
    {input}
    {loading && <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-primary" aria-hidden="true">
      <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />
    </span>}
    <span role="status" className="sr-only">{loading ? loadingText : ""}</span>
  </div>;
}
