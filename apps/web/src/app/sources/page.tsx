import { SuggestSourceLink } from "@/components/source-suggestion";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Rss } from "lucide-react";
import { getSources } from "@/lib/api";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { catalogOffset } from "@/lib/catalog-page";
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
  const offset = catalogOffset(query.offset);
  const sources = await getSources(offset, 60);
  return (
    <UserShell section="sources">
      <div className="page-heading">
        <div>
          <h1>Sources</h1>
        </div>
        <SuggestSourceLink />
      </div>
      {sources.length ? (
        <InfiniteCatalog key={offset} kind="sources" initialItems={sources} offset={offset} />
      ) : (
        <section className="empty-state">
          <div className="empty-icon">
            <Rss size={32} />
          </div>
          <h2>{offset ? "No sources on this page" : "No sources with published articles yet"}</h2>
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
      </nav>
    </UserShell>
  );
}
