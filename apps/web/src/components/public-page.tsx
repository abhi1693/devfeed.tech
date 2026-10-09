import { Suspense, type ReactNode } from "react";
import { PublicPageLoading } from "./public-page-loading";

async function ResolvedContent({ content }: { content: Promise<ReactNode> }) {
  return content;
}

/** Only wrap public results, after redirects and access checks have finished. */
export function PublicPage({
  content,
  kind = "feed",
}: {
  content: Promise<ReactNode>;
  kind?: "feed" | "sources" | "topics";
}) {
  return (
    <Suspense fallback={<PublicPageLoading kind={kind} />}>
      <ResolvedContent content={content} />
    </Suspense>
  );
}
