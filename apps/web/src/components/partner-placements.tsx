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
import { readerRequest, rememberReaderArticles } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import {
  parsePartnerPlacements,
  type PartnerPlacement,
  type PlacementQuery,
} from "@/lib/partner-placements";
import { recordPlacementView } from "@/lib/partner-placement-preferences";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { PARTNER_RECEIPT_TTL_MS, partnerReceiptExpiresAt } from "@/lib/partner-receipts";
import { useUser } from "./user-account";
import { useReaderPromptCoordinator } from "./reader-prompts";
import { displayHost } from "@/lib/feed-query";
import { ArticleImage } from "./article-image";
import { ArticleCard } from "./article-card";
import { EngagementProvider } from "./article-engagement";

// Simultaneously mounted feed/article contexts must never show the same catalog product twice.
const displayed = new Set<string>();

export type PartnerPlacementContext = PlacementQuery;
type Props = PartnerPlacementContext & {
  maxItems?: number;
  children?: (cards: ReactNode[], items: PartnerPlacement[]) => ReactNode;
  beforeDisplay?: () => void;
  afterDisplay?: () => void;
};
type Delivery = {
  context: string;
  owner: string;
  items: PartnerPlacement[];
  expiresAt: number;
  expired: boolean;
};

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
  const article = useRef<HTMLElement>(null);
  const content = item.ad_format === "sponsored_content" ? item.article : null;
  const card = preserveSpace && !["homepage_sponsorship", "engagement"].includes(item.ad_format);
  const sponsorshipId = useId();
  const tracking = useRef({
    receipt: item.receipt,
    completed: new Set<string>(),
    pending: new Set<string>(),
    viewed: false,
  });
  useEffect(() => {
    const target = content ? article.current : link.current;
    if (!active || !target) return;
    if (tracking.current.receipt !== item.receipt)
      tracking.current = {
        receipt: item.receipt,
        completed: new Set<string>(),
        pending: new Set<string>(),
        viewed: false,
      };
    const args = [
      target,
      item.receipt,
      () => {
        recordPlacementView(item.product_id);
      },
      tracking.current,
    ] as const;
    return content
      ? observePartnerPlacement(...args, "a.card-open-link, a.article-read-link")
      : observePartnerPlacement(...args);
  }, [item, active, content]);
  useEffect(() => {
    if (content) rememberReaderArticles([content]);
  }, [content]);
  if (content)
    return (
      <EngagementProvider articleIds={[content.id]}>
        <ArticleCard
          article={{
            ...content,
            sponsor_names: content.sponsor_names?.length
              ? content.sponsor_names
              : [item.partner_name],
          }}
          containerRef={article}
          promotion={{ productId: item.product_id, active, preserveSpace }}
        />
      </EngagementProvider>
    );
  return (
    <article
      className={`partner-placement ${card ? "partner-placement--card" : "partner-placement--compact"} partner-placement--${item.ad_format}`}
      data-product-id={item.product_id}
      data-ad-format={item.ad_format}
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
        {card ? (
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
            {item.ad_format !== "homepage_sponsorship" && item.description && (
              <p className="partner-placement-description">{item.description}</p>
            )}
            {item.ad_format !== "homepage_sponsorship" && (
              <span className="partner-placement-destination">
                <span>{displayHost(item.url)}</span>
              </span>
            )}
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
  ad_formats,
  topic_id,
  maxItems = 2,
  active,
  children,
  beforeDisplay,
  afterDisplay,
}: Props & { active: boolean }) {
  const { user, loading, sessionRevision } = useUser();
  const identity = user?.user_id ?? "anonymous";
  const formats = JSON.stringify(ad_formats ?? ["feed"]);
  const explicitFormats = ad_formats !== undefined;
  const context = JSON.stringify({
    surface,
    article_id,
    article_ids,
    allow_sponsored_fallback,
    formats,
    topic_id,
    identity,
    sessionRevision: user ? sessionRevision : 0,
  });
  const [result, setResult] = useState<Delivery | null>(null);
  const cached = useRef<Delivery | null>(null);
  const reserved = useRef(new Set<string>());
  const release = useCallback(() => {
    for (const id of reserved.current) displayed.delete(id);
    reserved.current.clear();
  }, []);
  const publish = useCallback(
    (next: Delivery) => {
      beforeDisplay?.();
      cached.current = next;
      setResult(next);
    },
    [beforeDisplay],
  );
  useLayoutEffect(() => afterDisplay?.(), [result, afterDisplay]);
  useEffect(() => {
    if (!result || result.expired) return;
    let timer: ReturnType<typeof setTimeout>;
    const check = () => {
      clearTimeout(timer);
      if (cached.current !== result) return;
      const remaining = result.expiresAt - Date.now();
      if (remaining <= 0) {
        release();
        // Retain inert feed cells so expiry cannot move the article being read.
        publish({ ...result, expired: true });
      } else timer = setTimeout(check, remaining);
    };
    check();
    // Suspended tabs may resume after the deadline without having run their timer.
    document.addEventListener("visibilitychange", check);
    window.addEventListener("focus", check);
    window.addEventListener("pageshow", check);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", check);
    };
  }, [result, publish, release]);
  const csrf = user?.csrf_token;
  useEffect(() => {
    if (!active || loading) return;
    function reserve(items: PartnerPlacement[]) {
      for (const item of items) {
        displayed.add(item.product_id);
        reserved.current.add(item.product_id);
      }
    }
    // Returning from a preview restores the same cells and tracking observers.
    // Expired cells stay suspended until a new reading context is mounted.
    if (cached.current?.context === context) {
      if (!cached.current.expired) {
        if (Date.now() >= cached.current.expiresAt) publish({ ...cached.current, expired: true });
        else reserve(cached.current.items);
      }
      return release;
    }
    const controller = new AbortController();
    const query: PlacementQuery = {
      surface,
      article_id,
      article_ids,
      ...(surface === "feed" ? { allow_sponsored_fallback } : {}),
      topic_id,
      ...(explicitFormats
        ? { ad_formats: JSON.parse(formats) as PlacementQuery["ad_formats"] }
        : {}),
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
        const loadedAt = Date.now();
        const candidates = parsePartnerPlacements(value);
        const requested = JSON.parse(formats) as string[];
        const eligible = candidates.filter(
          (item) =>
            !displayed.has(item.product_id) &&
            partnerReceiptExpiresAt(item.receipt) > loadedAt &&
            requested.includes(item.ad_format) &&
            (item.ad_format !== "homepage_sponsorship" ||
              (surface === "feed" && allow_sponsored_fallback)) &&
            (item.ad_format !== "sponsored_content" ||
              (surface === "feed" && item.article && !article_ids?.includes(item.article.id))),
        );
        const sponsored = maxItems === 1 ? eligible.find((item) => item.sponsored) : undefined;
        const items = (sponsored ? [sponsored] : eligible).slice(0, maxItems);
        reserve(items);
        publish({
          context,
          owner: identity,
          items,
          expiresAt: Math.min(
            loadedAt + PARTNER_RECEIPT_TTL_MS,
            ...items.map((item) => partnerReceiptExpiresAt(item.receipt)),
          ),
          expired: !items.length,
        });
      })
      .catch(() => {});
    return () => {
      controller.abort();
      release();
    };
  }, [
    context,
    active,
    surface,
    article_id,
    article_ids,
    allow_sponsored_fallback,
    formats,
    explicitFormats,
    topic_id,
    identity,
    loading,
    csrf,
    maxItems,
    publish,
    release,
  ]);
  // Pause a confirmed owner's cards during verification without collapsing reading geometry.
  // A changed owner cannot retain the previous account's delivery.
  const visible = active && !loading && result?.context === context && !result.expired;
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
  if (children) return children(cards, result?.owner === identity ? result.items : []);
  if (!cards.length) return null;
  return (
    <section className="partner-placements" aria-label="Products" hidden={!visible}>
      {cards}
    </section>
  );
}
