"use client";

/* eslint-disable @next/next/no-img-element -- Product logos use managed immutable assets. */
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import {
  parsePartnerPlacements,
  type PartnerPlacement,
  type PlacementQuery,
} from "@/lib/partner-placements";
import { recordPlacementView } from "@/lib/partner-placement-preferences";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { useUser } from "./user-account";
import { useReaderPromptCoordinator } from "./reader-prompts";
import { displayHost } from "@/lib/feed-query";
import { ArticleImage } from "./article-image";

// Simultaneously mounted feed/article contexts must never show the same catalog product twice.
const displayed = new Set<string>();

export type PartnerPlacementContext = PlacementQuery;
type Props = PartnerPlacementContext & {
  maxItems?: number;
  children?: (cards: ReactNode[]) => ReactNode;
  beforeDisplay?: () => void;
  afterDisplay?: () => void;
};
type Delivery = { context: string; owner: string; items: PartnerPlacement[]; loadedAt: number };

function ProductLogo({ name, url }: { name: string; url: string | null }) {
  const [failed, setFailed] = useState(false);
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
  return (
    <span className="partner-placement-logo" aria-hidden="true">
      {url && !failed ? (
        <img
          src={url}
          alt=""
          width={44}
          height={44}
          loading="lazy"
          referrerPolicy="no-referrer"
          ref={(image) => {
            if (image?.complete && image.naturalWidth === 0) setFailed(true);
          }}
          onError={() => setFailed(true)}
        />
      ) : (
        initials
      )}
    </span>
  );
}

function Placement({
  item,
  active,
  preserveSpace,
}: {
  item: PartnerPlacement;
  active: boolean;
  preserveSpace: boolean;
}) {
  const link = useRef<HTMLAnchorElement>(null);
  const sponsorshipId = useId();
  const tracking = useRef({
    receipt: item.receipt,
    completed: new Set<string>(),
    pending: new Set<string>(),
    viewed: false,
  });
  useEffect(() => {
    if (!active || !link.current) return;
    if (tracking.current.receipt !== item.receipt)
      tracking.current = {
        receipt: item.receipt,
        completed: new Set<string>(),
        pending: new Set<string>(),
        viewed: false,
      };
    return observePartnerPlacement(
      link.current,
      item.receipt,
      () => {
        recordPlacementView(item.product_id);
      },
      tracking.current,
    );
  }, [item, active]);
  return (
    <article
      className={`partner-placement ${preserveSpace ? "partner-placement--card" : "partner-placement--compact"}`}
      data-product-id={item.product_id}
      data-suspended={!active && preserveSpace ? "true" : undefined}
      hidden={!active && !preserveSpace}
      aria-hidden={!active || undefined}
      inert={!active}
    >
      <a
        ref={link}
        href={item.url}
        target="_blank"
        rel="sponsored noopener noreferrer"
        className="partner-placement-link"
        aria-label={item.name}
        aria-describedby={sponsorshipId}
        referrerPolicy="no-referrer"
      >
        {preserveSpace ? (
          <>
            <div className="card-image">
              <ArticleImage
                src={item.image_url ?? undefined}
                variants={item.image_variants}
                label={item.name}
                fallback={
                  <span className="partner-placement-hero-logo">
                    <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />
                  </span>
                }
              />
            </div>
            <div className="card-copy">
              <h2>
                <span>{item.name}</span>
              </h2>
              {item.description && (
                <p className="partner-placement-description">{item.description}</p>
              )}
              <div className="card-meta-row">
                <span className="card-source">{item.partner_name}</span>
                <span
                  id={sponsorshipId}
                  className="content-type card-meta-type partner-placement-label"
                  data-promotion-kind="sponsored"
                >
                  Sponsored
                </span>
              </div>
              <div className="card-bottom partner-placement-destination">
                <span>{displayHost(item.url)}</span>
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="partner-placement-identity">
              <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />
              <div>
                <strong>{item.name}</strong>
                <span
                  id={sponsorshipId}
                  className="content-type card-meta-type partner-placement-label"
                  data-promotion-kind="sponsored"
                >
                  Sponsored
                </span>
              </div>
            </div>
            {item.description && (
              <p className="partner-placement-description">{item.description}</p>
            )}
            <span className="partner-placement-destination">
              <span>{displayHost(item.url)}</span>
            </span>
          </>
        )}
      </a>
    </article>
  );
}

/** Optional contextual recommendations load independently of article and feed rendering. */
export function PartnerPlacements({ children, ...query }: Props) {
  const prompts = useReaderPromptCoordinator();
  const active =
    !prompts ||
    (prompts.ready && (query.surface === "article" ? prompts.modalOpen : !prompts.modalOpen));
  return (
    <PlacementDelivery {...query} active={active}>
      {children}
    </PlacementDelivery>
  );
}

function PlacementDelivery({
  surface,
  article_id,
  article_ids,
  allow_sponsored_fallback = false,
  topic_id,
  maxItems = 2,
  active,
  children,
  beforeDisplay,
  afterDisplay,
}: Props & { active: boolean }) {
  const { user, loading, sessionRevision } = useUser();
  const identity = user?.user_id ?? "anonymous";
  const context = JSON.stringify({
    surface,
    article_id,
    article_ids,
    allow_sponsored_fallback,
    topic_id,
    identity,
    sessionRevision: user ? sessionRevision : 0,
  });
  const [result, setResult] = useState<Delivery | null>(null);
  const cached = useRef<Delivery | null>(null);
  const publish = useCallback(
    (next: Delivery) => {
      beforeDisplay?.();
      cached.current = next;
      setResult(next);
    },
    [beforeDisplay],
  );
  useLayoutEffect(() => afterDisplay?.(), [result, afterDisplay]);
  const csrf = user?.csrf_token;
  useEffect(() => {
    if (!active || loading) return;
    const controller = new AbortController();
    const reserved: string[] = [];
    function reserve(items: PartnerPlacement[]) {
      for (const item of items) {
        displayed.add(item.product_id);
        reserved.push(item.product_id);
      }
    }
    // Returning from a preview restores the same cells and tracking observers.
    // An expired delivery stays empty until a new reading context is mounted.
    if (cached.current?.context === context) {
      if (Date.now() - cached.current.loadedAt >= 60 * 60_000)
        publish({ ...cached.current, items: [] });
      else reserve(cached.current.items);
      return () => {
        for (const id of reserved) displayed.delete(id);
      };
    }
    const query: PlacementQuery = {
      surface,
      article_id,
      article_ids,
      ...(surface === "feed" ? { allow_sponsored_fallback } : {}),
      topic_id,
      exclude_product_ids: [...displayed].slice(0, 100),
    };
    const init = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(identity !== "anonymous" && csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify(query),
      cache: "no-store" as const,
      referrerPolicy: "no-referrer" as const,
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
    };
    const personalized = identity !== "anonymous";
    const load = personalized
      ? userRequest<unknown>("partner-placements/query", init)
      : readerRequest("/api/v1/partner-placements/query", { ...init, credentials: "omit" }).then(
          async (response) => {
            if (!response.ok) throw new Error("Placements unavailable");
            return response.json() as Promise<unknown>;
          },
        );
    void load
      .then((value) => {
        if (controller.signal.aborted) return;
        const candidates = parsePartnerPlacements(value);
        const eligible = candidates.filter((item) => !displayed.has(item.product_id));
        const sponsored = maxItems === 1 ? eligible.find((item) => item.sponsored) : undefined;
        const items = (sponsored ? [sponsored] : eligible).slice(0, maxItems);
        reserve(items);
        publish({ context, owner: identity, items, loadedAt: Date.now() });
      })
      .catch(() => {});
    return () => {
      controller.abort();
      for (const id of reserved) displayed.delete(id);
    };
  }, [
    context,
    active,
    surface,
    article_id,
    article_ids,
    allow_sponsored_fallback,
    topic_id,
    identity,
    loading,
    csrf,
    maxItems,
    publish,
  ]);
  // Pause a confirmed owner's cards during verification without collapsing reading geometry.
  // A changed owner cannot retain the previous account's delivery.
  const visible = active && !loading && result?.context === context;
  const cards =
    result?.owner === identity
      ? result.items.map((item) => (
          <Placement
            key={item.product_id}
            item={item}
            active={visible}
            preserveSpace={!!children}
          />
        ))
      : [];
  if (children) return children(cards);
  if (!cards.length) return null;
  return (
    <section className="partner-placements" aria-label="Products" hidden={!visible}>
      {cards}
    </section>
  );
}
