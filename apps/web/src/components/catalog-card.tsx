import Link from "next/link";
import type { ReactNode } from "react";
import { CatalogIcon } from "./catalog-icon";

export function CatalogCard({
  href,
  name,
  logoUrl,
  description,
  followAction,
  source = false,
}: {
  href: string;
  name: string;
  logoUrl: string | null;
  description?: ReactNode;
  followAction: ReactNode;
  source?: boolean;
}) {
  const linkClassName = source ? "source-card-link" : "topic-card-link";
  return (
    <article className={`topic-card catalog-card${source ? " source-card" : ""}`}>
      <Link href={href} className={linkClassName}>
        <div className="topic-card-heading">
          <CatalogIcon url={logoUrl} source={source} />
          <h2>{name}</h2>
        </div>
        {description}
      </Link>
      {followAction}
    </article>
  );
}
