"use client";

import type { ReactNode } from "react";
import { PartnerPlacements, type PartnerPlacementContext } from "./partner-placements";
import { useReadingAnchor } from "./use-reading-anchor";

export function CollectionFeedPlacements({
  context,
  children,
}: {
  context?: PartnerPlacementContext;
  children: ReactNode;
}) {
  const { ref, beforeDisplay, afterDisplay } = useReadingAnchor(
    ".article-card, .article-list-row:not(.partner-placement)",
  );
  const content = (cards: ReactNode[] = []) => (
    <div
      ref={ref}
      className={`collection-feed-layout${cards.length ? " collection-feed-layout--sidebar" : ""}`}
    >
      <div className="collection-feed-articles">{children}</div>
      {cards.length > 0 && (
        <aside className="collection-feed-sidebar" aria-label="Related product">
          <section className="partner-placements" aria-label="Sponsored product">
            {cards}
          </section>
        </aside>
      )}
    </div>
  );
  return context ? (
    <PartnerPlacements
      {...context}
      maxItems={1}
      beforeDisplay={beforeDisplay}
      afterDisplay={afterDisplay}
    >
      {content}
    </PartnerPlacements>
  ) : (
    content()
  );
}
