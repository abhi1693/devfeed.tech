import type { Metadata } from "next";
import { loadArticle } from "@/lib/article";
import { FeedView } from "@/components/feed-view";
import { ArticleModal } from "@/components/article-modal";
import { parseFilters } from "@/lib/feed-query";
import { ArticlePreview } from "@/components/article-preview";
import { articleMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
type Props = { params: Promise<{ slug: string }> };
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const article = await loadArticle((await params).slug);
  return articleMetadata(article);
}
export default async function ArticlePage({ params }: Props) {
  const article = await loadArticle((await params).slug);
  return (
    <>
      {await FeedView({ filters: parseFilters({}), structuredData: false })}
      <ArticleModal direct slug={article.slug}>
        <ArticlePreview article={article} />
      </ArticleModal>
    </>
  );
}
