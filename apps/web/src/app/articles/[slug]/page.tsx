import { ArticleUnavailable } from "@/components/article-unavailable";
import { RetryFeed } from "@/components/retry-feed";
import type { Metadata } from "next";
import { loadArticle } from "@/lib/article";
import { FeedView } from "@/components/feed-view";
import { ArticleModal } from "@/components/article-modal";
import { parseFilters } from "@/lib/feed-query";
import { ArticlePreview } from "@/components/article-preview";
import { articleMetadata } from "@/lib/metadata";
import { Suspense, type ReactNode } from "react";
import { headers } from "next/headers";
import { UserShell } from "@/components/user-shell";
import { LoadingSkeleton } from "@/components/loading-skeleton";
// Run with the response nonce as soon as the streamed dialog reaches the parser.
// Native modality must not wait for the reader bundle or reopen during hydration.
const openDirectArticle = `{
  const dialog = document.querySelector('dialog.article-modal[data-direct-entry][open]');
  if (dialog && !dialog.matches(':modal')) {
    dialog.close();
    dialog.showModal();
    dialog.addEventListener('cancel', (event) => {
      if (!dialog.dataset.hydrated) {
        event.preventDefault();
        location.replace('/');
      }
    });
  }
}`;
async function DirectArticle({ slug, children }: { slug: string; children: ReactNode }) {
  const nonce = (await headers()).get("content-security-policy")?.match(/'nonce-([^']+)'/)?.[1];
  return (
    <>
      <ArticleModal direct slug={slug}>
        {children}
      </ArticleModal>
      <script nonce={nonce} dangerouslySetInnerHTML={{ __html: openDirectArticle }} />
    </>
  );
}
export const dynamic = "force-dynamic";
type Props = { params: Promise<{ slug: string }> };
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const article = await loadArticle((await params).slug);
  return article
    ? articleMetadata(article)
    : { title: "Article temporarily unavailable", robots: { index: false, follow: false } };
}
export default async function ArticlePage({ params }: Props) {
  const { slug } = await params;
  const article = await loadArticle(slug);
  if (!article)
    return (
      <DirectArticle slug={slug}>
        <ArticleUnavailable>
          <RetryFeed />
        </ArticleUnavailable>
      </DirectArticle>
    );
  return (
    <>
      <DirectArticle slug={article.slug}>
        <ArticlePreview article={article} />
      </DirectArticle>
      <Suspense
        fallback={
          <UserShell>
            <LoadingSkeleton />
          </UserShell>
        }
      >
        <FeedView filters={parseFilters({})} structuredData={false} />
      </Suspense>
    </>
  );
}
