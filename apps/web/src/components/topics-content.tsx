import Link from "@/components/reader-link";
import { Hash } from "lucide-react";
import type { ReactNode } from "react";
import type { CatalogPage } from "@/lib/catalog-page";
import type { Topic } from "@/lib/types";
import { CatalogContent } from "./catalog-content";

export function TopicsContent({
  topics,
  offset,
  initialPage,
  children,
  withShell,
}: {
  topics: Topic[];
  offset: number;
  initialPage?: CatalogPage<Topic>;
  children?: ReactNode;
  withShell?: boolean;
}) {
  return (
    <CatalogContent
      withShell={withShell}
      kind="topics"
      title="Explore topics"
      items={topics}
      offset={offset}
      initialPage={initialPage}
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
