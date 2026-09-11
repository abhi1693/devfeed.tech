import Link from "next/link";
import { ArrowLeft, ArrowRight, Rss } from "lucide-react";
import { getSources } from "@/lib/api";
import { CatalogIcon } from "@/components/catalog-icon";
import { UserShell } from "@/components/user-shell";
import type { SearchParams } from "@/lib/feed-query";
export const dynamic = "force-dynamic";
export const metadata = { title: "Sources" };
export default async function Sources({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const offset =
    typeof query.offset === "string" && /^\d+$/.test(query.offset)
      ? Math.min(Number(query.offset), 1000000)
      : 0;
  const sources = await getSources(offset, 60);
  return (
    <UserShell section="sources">
      <div className="page-heading">
        <div>
          <h1>Sources</h1>
        </div>
      </div>
      {sources.length ? (
        <div className="topic-grid">
          {sources.map((source) => (
            <Link
              key={source.id}
              href={`/sources/${source.id}`}
              className="topic-card catalog-card"
            >
              <div className="topic-card-heading">
                <CatalogIcon url={source.logo_url} source />
                <h2>{source.name}</h2>
              </div>
              {source.description && <p>{source.description}</p>}
            </Link>
          ))}
        </div>
      ) : (
        <section className="empty-state">
          <div className="empty-icon">
            <Rss size={32} />
          </div>
          <h2>No sources on this page</h2>
          <Link className="button primary" href="/topics">
            Explore topics
            <ArrowRight size={16} />
          </Link>
        </section>
      )}
      <nav className="pagination" aria-label="Source pages">
        {offset > 0 && (
          <Link
            className="button"
            href={`/sources?offset=${Math.max(0, offset - 60)}`}
          >
            <ArrowLeft size={16} />
            Previous sources
          </Link>
        )}
        {sources.length === 60 && (
          <Link className="button" href={`/sources?offset=${offset + 60}`}>
            More sources
            <ArrowRight size={16} />
          </Link>
        )}
      </nav>
    </UserShell>
  );
}
