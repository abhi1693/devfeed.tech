import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { InfiniteCatalog } from "./infinite-catalog";
import { UserShell } from "./user-shell";
import { catalogPage, type CatalogPage } from "@/lib/catalog-page";
import type { Source, Topic } from "@/lib/types";

type CatalogKind = "topics" | "sources";

export function CatalogContent({
  kind,
  title,
  items,
  offset,
  initialPage,
  children,
  headerAction,
  emptyIcon,
  emptyTitle,
  emptyAction,
}: {
  kind: CatalogKind;
  title: string;
  items: (Source | Topic)[];
  offset: number;
  initialPage?: CatalogPage<Source | Topic>;
  children?: ReactNode;
  headerAction?: ReactNode;
  emptyIcon: ReactNode;
  emptyTitle: { firstPage: string; paginated: string };
  emptyAction: ReactNode;
}) {
  const label = kind === "topics" ? "Topic" : "Source";
  return (
    <UserShell section={kind}>
      {children}
      <div className="page-heading">
        <div>
          <h1>{title}</h1>
        </div>
        {headerAction}
      </div>
      {items.length ? (
        <InfiniteCatalog
          key={offset}
          kind={kind}
          initialPage={initialPage ?? catalogPage(items, offset)}
        />
      ) : (
        <section className="empty-state">
          {emptyIcon}
          <h2>{offset ? emptyTitle.paginated : emptyTitle.firstPage}</h2>
          {emptyAction}
        </section>
      )}
      <nav className="pagination" aria-label={`${label} pages`}>
        {offset > 0 && (
          <Link className="button" href={`/${kind}?offset=${Math.max(0, offset - 60)}`}>
            <ArrowLeft size={16} />
            Previous {kind}
          </Link>
        )}
      </nav>
    </UserShell>
  );
}
