import Link from "next/link";
import { Hash } from "lucide-react";
import type { ReactNode } from "react";
import type { Topic } from "@/lib/types";
import { CatalogContent } from "./catalog-content";

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
    <CatalogContent
      kind="topics"
      title="Explore topics"
      items={topics}
      offset={offset}
      emptyIcon={<Hash size={32} />}
      emptyTitle={{
        firstPage: "No topics with published articles yet",
        paginated: "No topics on this page",
      }}
      emptyAction={
        <Link className="button" href="/latest">
          Back to the feed
        </Link>
      }
    >
      {children}
    </CatalogContent>
  );
}
