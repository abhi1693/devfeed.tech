import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { loadArticle } from "@/lib/article";
import { UserShell } from "@/components/user-shell";
import { ArticlePreview } from "@/components/article-preview";
export const dynamic = "force-dynamic";
type Props = { params: Promise<{ id: string }> };
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const article = await loadArticle((await params).id);
  return {
    title: article.title,
    description: (article.summary || article.ai_summary || article.title).slice(
      0,
      180,
    ),
  };
}
export default async function ArticlePage({ params }: Props) {
  const article = await loadArticle((await params).id);
  return (
    <UserShell section="article">
      <div className="article-detail">
        <Link href="/" className="back-link">
          <ArrowLeft size={16} />
          Back to the feed
        </Link>
        <ArticlePreview article={article} />
      </div>
    </UserShell>
  );
}
