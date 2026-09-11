import { loadArticle } from "@/lib/article";
import { ArticlePreview } from "@/components/article-preview";
import { ArticleModal } from "@/components/article-modal";
export const dynamic = "force-dynamic";
export default async function Preview({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  return (
    <ArticleModal>
      <ArticlePreview article={await loadArticle((await params).slug)} />
    </ArticleModal>
  );
}
