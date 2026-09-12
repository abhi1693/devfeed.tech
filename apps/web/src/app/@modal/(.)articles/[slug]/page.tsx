import { canonicalUrl } from "@/lib/metadata";
import { loadArticle } from "@/lib/article";
import { ArticlePreview } from "@/components/article-preview";
import { ArticleModal } from "@/components/article-modal";
export const dynamic = "force-dynamic";
export { generateMetadata } from "@/app/articles/[slug]/page";
export default async function Preview({ params }: { params: Promise<{ slug: string }> }) {
  const article = await loadArticle((await params).slug);
  return (
    <ArticleModal canonical={canonicalUrl(`/articles/${encodeURIComponent(article.slug)}`)}>
      <ArticlePreview article={article} />
    </ArticleModal>
  );
}
