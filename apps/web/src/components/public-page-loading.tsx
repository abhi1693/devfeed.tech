import { LoadingSkeleton } from "./loading-skeleton";
import { UserShell } from "./user-shell";

/** Stream navigation while public data is loading; never retain request HTML. */
export function PublicPageLoading({ kind = "feed" }: { kind?: "feed" | "sources" | "topics" }) {
  return (
    <UserShell section={kind}>
      <LoadingSkeleton
        kind={kind}
        label={kind === "feed" ? "Loading articles…" : `Loading ${kind}…`}
      />
    </UserShell>
  );
}
