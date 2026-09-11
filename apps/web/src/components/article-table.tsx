import { UserDate } from "./user-date";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { displayHost } from "@/lib/feed-query";
import { ArticleEngagement } from "./article-engagement";

export function ArticleTable({ articles, recommendations, showHeader }: {
  articles: Article[];
  recommendations: Record<string, string>;
  showHeader: boolean;
}) {
  return <div className="article-table-wrap">
    <table className="article-table">
      <caption className="sr-only">Articles in compact view</caption>
      <colgroup><col className="article-column" /><col className="source-column" /><col className="date-column" /><col className="activity-column" /></colgroup>
      <thead className={showHeader ? undefined : "sr-only"}><tr>
        <th scope="col">Article</th><th scope="col" className="source-column">Source</th><th scope="col" className="date-column">Published</th><th scope="col">Activity</th>
      </tr></thead>
      <tbody>{articles.map(article => {
        const source = article.sources[0];
        const date = article.published_at ?? article.feed_at;
        return <tr key={article.id} className="article-list-row">
          <td className="article-list-main">
            <Link className="article-list-title" href={`/articles/${article.slug}`} scroll={false} prefetch={false}>{article.title}</Link>
            <div className="article-list-meta">
              <span className="article-list-type">{article.content_type}</span>
              {article.topics.slice(0, 2).map(topic => <Link key={topic.id} href={`/topics/${encodeURIComponent(topic.slug)}`}>{topic.name}</Link>)}
              <span className="article-list-mobile-source">{source?.name ?? displayHost(article.canonical_url)} · <UserDate value={date} /></span>
              {recommendations[article.id] && <span className="recommendation-reason">{recommendations[article.id]}</span>}
            </div>
          </td>
          <td className="source-column">{source ? <Link href={`/sources/${source.id}`}>{source.name}</Link> : displayHost(article.canonical_url)}</td>
          <td className="date-column"><UserDate value={date} /></td>
          <td className="activity-column"><ArticleEngagement articleId={article.id} articleSlug={article.slug} /></td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}
