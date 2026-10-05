import { UserDate } from "./user-date";
import Link from "@/components/reader-link";
import type { Article } from "@/lib/types";
import { displayHost, sourceHref } from "@/lib/feed-query";
import { ArticleBookmarkButton } from "./article-engagement";
import type { ReactNode } from "react";

export function ArticleTableFrame({
  children,
  showHeader,
}: {
  children: ReactNode;
  showHeader: boolean;
}) {
  return (
    <div className="article-table-wrap">
      <table className="article-table">
        <caption className="sr-only">Articles in compact view</caption>
        <colgroup>
          <col className="article-column" />
          <col className="source-column" />
          <col className="date-column" />
          <col className="bookmark-column" />
        </colgroup>
        <thead className={showHeader ? undefined : "sr-only"}>
          <tr>
            <th scope="col">Article</th>
            <th scope="col" className="source-column">
              Source
            </th>
            <th scope="col" className="date-column">
              Published
            </th>
            <th scope="col" className="bookmark-column">
              <span className="sr-only">Bookmark</span>
            </th>
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function ArticleTable({
  articles,
  showHeader,
}: {
  articles: Article[];
  showHeader: boolean;
}) {
  return (
    <ArticleTableFrame showHeader={showHeader}>
      {articles.map((article) => {
        const source = article.sources[0];
        const sourceName = source?.name ?? displayHost(article.canonical_url);
        const date = article.published_at ?? article.feed_at;
        return (
          <tr key={article.id} className="article-list-row">
            <td className="article-list-main">
              <Link
                className="article-list-title"
                title={article.title}
                href={`/articles/${article.slug}`}
                scroll={false}
                prefetch={false}
              >
                {article.title}
              </Link>
            </td>
            <td className="source-column">
              {source ? (
                <Link href={sourceHref(source)} title={sourceName}>
                  {sourceName}
                </Link>
              ) : (
                <span title={sourceName}>{sourceName}</span>
              )}
            </td>
            <td className="date-column">
              <UserDate value={date} />
            </td>
            <td className="bookmark-column">
              <ArticleBookmarkButton articleId={article.id} articleSlug={article.slug} />
            </td>
          </tr>
        );
      })}
    </ArticleTableFrame>
  );
}
