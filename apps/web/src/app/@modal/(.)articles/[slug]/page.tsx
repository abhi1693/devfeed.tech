import { ArticleUnavailable } from "@/components/article-unavailable";
import { RetryFeed } from "@/components/retry-feed";
import { canonicalUrl } from "@/lib/metadata";
import { loadArticle } from "@/lib/article";
import { ArticlePreview } from "@/components/article-preview";
import { ArticleModal } from "@/components/article-modal";
export const dynamic = "force-dynamic";
export { generateMetadata } from "@/app/articles/[slug]/page";
export default async function Preview({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const article = await loadArticle(slug);
  if (!article)
    return (
      <ArticleModal>
        <ArticleUnavailable>
          <RetryFeed />
        </ArticleUnavailable>
      </ArticleModal>
    );
  return (
    <ArticleModal canonical={canonicalUrl(`/articles/${encodeURIComponent(article.slug)}`)}>
      <ArticlePreview article={article} />
    </ArticleModal>
  );
}
