import { SuggestSourceLink } from "@/components/source-suggestion";
import Link from "next/link";
import { ArrowRight, Rss } from "lucide-react";
import type { ReactNode } from "react";
import type { Source } from "@/lib/types";
import { CatalogContent } from "./catalog-content";

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
    <CatalogContent
      kind="sources"
      title="Sources"
      items={sources}
      offset={offset}
      headerAction={<SuggestSourceLink />}
      emptyIcon={
        <div className="empty-icon">
          <Rss size={32} />
        </div>
      }
      emptyTitle={{
        firstPage: "No sources with published articles yet",
        paginated: "No sources on this page",
      }}
      emptyAction={
        <Link className="button primary" href="/topics">
          Explore topics
          <ArrowRight size={16} />
        </Link>
      }
    >
      {children}
    </CatalogContent>
  );
}
