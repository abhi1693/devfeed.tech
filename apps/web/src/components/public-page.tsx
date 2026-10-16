import { Suspense, type ReactNode } from "react";
import { PublicPageLoading } from "./public-page-loading";
import { UserShell } from "./user-shell";
import type { FeedFilters } from "@/lib/feed-query";

async function ResolvedContent({ content }: { content: Promise<ReactNode> }) {
  return content;
}

/** Only wrap public results, after redirects and access checks have finished. */
export function PublicPage({
  content,
  kind = "feed",
  filters,
}: {
  content: Promise<ReactNode>;
  filters?: FeedFilters;
  kind?: "feed" | "sources" | "topics";
}) {
  return (
    <UserShell section={kind} filters={filters}>
      <Suspense fallback={<PublicPageLoading kind={kind} />}>
        <ResolvedContent content={content} />
      </Suspense>
    </UserShell>
  );
}
