import { SuggestSourceLink } from "@/components/source-suggestion";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Rss } from "lucide-react";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { UserShell } from "@/components/user-shell";
import type { ReactNode } from "react";
import type { Source } from "@/lib/types";

export function SourcesContent({
  sources,
  offset,
  children,
}: {
  sources: Source[];
  offset: number;
  children?: ReactNode;
}) {
  return (
    <UserShell section="sources">
      {children}
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
