import { LoadingSkeleton } from "./loading-skeleton";

/** Replace only public results while the surrounding shell stays interactive. */
export function PublicPageLoading({ kind = "feed" }: { kind?: "feed" | "sources" | "topics" }) {
  return (
    <LoadingSkeleton
      kind={kind}
      label={kind === "feed" ? "Loading articles…" : `Loading ${kind}…`}
    />
  );
}
