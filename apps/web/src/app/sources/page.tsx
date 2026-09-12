import { SuggestSourceLink } from "@/components/source-suggestion";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Rss } from "lucide-react";
import { getSources } from "@/lib/api";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { catalogOffset } from "@/lib/catalog-page";
import { UserShell } from "@/components/user-shell";
import type { SearchParams } from "@/lib/feed-query";
import { JsonLd } from "@/components/json-ld";
import { collectionStructuredData } from "@/lib/structured-data";
import { catalogCanonical, pageMetadata } from "@/lib/metadata";
export const dynamic = "force-dynamic";
export async function generateMetadata({ searchParams }: { searchParams: Promise<SearchParams> }) {
  return pageMetadata(
    "Sources",
    "Discover sources publishing developer news, tutorials, and releases.",
    catalogCanonical("/sources", await searchParams),
  );
}
export default async function Sources({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const offset = catalogOffset(query.offset);
  const sources = await getSources(offset, 60);
  return (
    <UserShell section="sources">
      <JsonLd
        data={collectionStructuredData(
          catalogCanonical("/sources", query),
          "Sources",
          sources.map((item) => ({
            name: item.name,
            path: `/sources/${encodeURIComponent(item.slug)}`,
          })),
          { offset },
        )}
      />
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
          <Link className="button" href={`/sources?offset=${Math.max(0, offset - 60)}`}>
            <ArrowLeft size={16} />
            Previous sources
          </Link>
        )}
      </nav>
    </UserShell>
  );
}
