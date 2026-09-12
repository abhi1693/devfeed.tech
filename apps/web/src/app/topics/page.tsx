import Link from "next/link";
import { ArrowLeft, Hash } from "lucide-react";
import { getTopics } from "@/lib/api";
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
    "Explore topics",
    "Explore developer topics and find the latest published articles.",
    catalogCanonical("/topics", await searchParams),
  );
}
export default async function Topics({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const offset = catalogOffset(query.offset);
  const topics = await getTopics(offset);
  return (
    <UserShell section="topics">
      <JsonLd
        data={collectionStructuredData(
          catalogCanonical("/topics", query),
          "Explore topics",
          topics.map((item) => ({
            name: item.name,
            path: `/topics/${encodeURIComponent(item.slug)}`,
          })),
          { offset },
        )}
      />
      <div className="page-heading">
        <div>
          <h1>Explore topics</h1>
        </div>
      </div>
      {topics.length ? (
        <InfiniteCatalog key={offset} kind="topics" initialItems={topics} offset={offset} />
      ) : (
        <section className="empty-state">
          <Hash size={32} />
          <h2>{offset ? "No topics on this page" : "No topics with published articles yet"}</h2>
          <Link className="button" href="/">
            Back to the feed
          </Link>
        </section>
      )}
      <nav className="pagination" aria-label="Topic pages">
        {offset > 0 && (
          <Link className="button" href={`/topics?offset=${Math.max(0, offset - 60)}`}>
            <ArrowLeft size={16} />
            Previous topics
          </Link>
        )}
      </nav>
    </UserShell>
  );
}
