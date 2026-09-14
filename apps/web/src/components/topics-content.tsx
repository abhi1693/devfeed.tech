import Link from "next/link";
import { ArrowLeft, Hash } from "lucide-react";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { UserShell } from "@/components/user-shell";
import type { ReactNode } from "react";
import type { Topic } from "@/lib/types";

export function TopicsContent({
  topics,
  offset,
  children,
}: {
  topics: Topic[];
  offset: number;
  children?: ReactNode;
}) {
  return (
    <UserShell section="topics">
      {children}
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
