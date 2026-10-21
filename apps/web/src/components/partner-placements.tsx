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
import { ArrowUpRight } from "lucide-react";
import { userRequest } from "@/lib/user";
import {
  parsePartnerPlacements,
  type PartnerPlacement,
  type PlacementQuery,
} from "@/lib/partner-placements";
import {
  observePartnerPlacement,
  type PartnerPlacementTrackingState,
} from "@/lib/partner-tracking";
import { PARTNER_RECEIPT_TTL_MS, partnerReceiptExpiresAt } from "@/lib/partner-receipts";
import { useUser } from "./user-account";
import { useReaderPromptCoordinator } from "./reader-prompts";
import { displayHost } from "@/lib/feed-query";
import { ArticleImage } from "./article-image";
import { ArticleCard } from "./article-card";
import { ArticleStackedRow } from "./article-stacked";
import { CatalogIcon } from "./catalog-icon";
import { EngagementProvider } from "./article-engagement";
import { adLinkRel } from "@devfeed/ui/ad-link-rel";
import type { ColumnRequestQueue } from "@/lib/column-feed";

// Simultaneously mounted feed/article contexts must never show the same catalog product twice.
const displayed = new Set<string>();

export type PartnerPlacementContext = PlacementQuery;
type Props = PartnerPlacementContext & {
  maxItems?: number;
  layout?: "table" | "stacked" | "columns";
  requestQueue?: ColumnRequestQueue;
  children?: (cards: ReactNode[], items: PartnerPlacement[], expired: boolean) => ReactNode;
  beforeDisplay?: () => void;
  afterDisplay?: () => void;
  onDelivery?: (items: PartnerPlacement[]) => void;
};
type Delivery = {
  context: string;
  owner: string;
  items: PartnerPlacement[];
  tracking: Map<string, PartnerPlacementTrackingState>;
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
  visible,
  preserveSpace,
  layout,
  tracking,
}: {
  item: PartnerPlacement;
  active: boolean;
  visible: boolean;
  preserveSpace: boolean;
  layout?: "table" | "stacked" | "columns";
  tracking?: PartnerPlacementTrackingState;
}) {
  const link = useRef<HTMLAnchorElement>(null);
  const article = useRef<HTMLElement>(null);
  const content = item.ad_format === "sponsored_content" ? item.article : null;
  const table = layout === "table" && item.ad_format === "feed";
  const Content = layout === "stacked" ? ArticleStackedRow : ArticleCard;
  const card = preserveSpace && !["header_sponsorship", "engagement"].includes(item.ad_format);
  const image = item.image_url ?? item.image_variants[0]?.url;
  const platformName = item.platform?.name ?? item.partner_name;
  const attribution = item.attribution?.trim();
  const sponsorshipId = useId();
  useEffect(() => {
    const target = content ? article.current : link.current;
    if (!active || !target || !tracking) return;
    const args = [target, item.receipt, tracking] as const;
    return content
      ? observePartnerPlacement(...args, "a.card-open-link, a.article-read-link")
      : observePartnerPlacement(...args);
  }, [item, active, content, layout, tracking]);
  useEffect(() => {
    if (content) rememberReaderArticles([content]);
  }, [content]);
  const stateProps = {
    "data-product-id": item.product_id,
    "data-ad-format": item.ad_format,
    "data-suspended": !visible && preserveSpace ? "true" : undefined,
    hidden: !visible && !preserveSpace,
    "aria-hidden": !active || undefined,
    inert: !active,
  };
  const linkProps = {
    ref: link,
    href: item.url,
    target: "_blank",
    rel: adLinkRel(item.link_rel),
    "aria-label": item.name,
    "aria-describedby": sponsorshipId,
    title:
      item.ad_format === "header_sponsorship"
        ? `${item.name} · ${displayHost(item.url)}`
        : table
          ? item.name
          : undefined,
    referrerPolicy: "no-referrer" as const,
  };
  if (table)
    return (
      <tr
        className="article-list-row partner-placement partner-placement--row partner-placement--feed"
        {...stateProps}
      >
        <td className="article-list-main">
          <a {...linkProps} className="partner-placement-link">
            <span id={sponsorshipId} className="sr-only">
              Sponsored
            </span>
            <span className="partner-placement-title">
              <strong>{item.name}</strong>
              <span className="partner-placement-separator" aria-hidden="true">
                |
              </span>
              <span className="partner-placement-description" title={item.tagline}>
                {item.tagline}
              </span>
              <span
                className="content-type card-meta-type partner-placement-label partner-placement-mobile-label"
                data-promotion-kind="sponsored"
                aria-hidden="true"
              >
                Sponsored
              </span>
            </span>
          </a>
        </td>
        <td className="source-column">
          <span title={platformName}>{platformName}</span>
        </td>
        <td className="date-column">
          <span
            className="content-type card-meta-type partner-placement-label"
            data-promotion-kind="sponsored"
            aria-hidden="true"
          >
            Sponsored
          </span>
        </td>
        <td className="bookmark-column" />
      </tr>
    );
  if (content)
    return (
      <EngagementProvider articleIds={[content.id]}>
        <Content
          article={{
            ...content,
            sponsor_names: content.sponsor_names?.length
              ? content.sponsor_names
              : [item.partner_name],
          }}
          containerRef={article}
          promotion={{ productId: item.product_id, active, visible, preserveSpace }}
        />
      </EngagementProvider>
    );
  if (layout === "columns")
    return (
      <article
        className="article-stacked-row partner-placement partner-placement--column"
        {...stateProps}
      >
        <a
          {...linkProps}
          className="partner-placement-link"
          aria-describedby={`${sponsorshipId} ${sponsorshipId}-attribution`}
        >
          <div className="column-placement-meta">
            <span
              id={sponsorshipId}
              className="partner-placement-label"
              data-promotion-kind="sponsored"
            >
              Sponsored
            </span>
            <span
              id={`${sponsorshipId}-attribution`}
              className="column-placement-attribution"
              title={attribution || platformName}
            >
              {attribution || platformName}
            </span>
          </div>
          <h2 className="column-placement-title">
            <span title={item.name}>{item.name}</span>
            <ArrowUpRight size={16} aria-hidden="true" />
          </h2>
          {item.tagline && <p className="partner-placement-description">{item.tagline}</p>}
        </a>
      </article>
    );
  if (layout === "stacked")
    return (
      <article
        className="article-stacked-row partner-placement partner-placement--stacked"
        {...stateProps}
      >
        <a {...linkProps} className="partner-placement-link">
          <div className="stacked-placement-brand">
            <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />
          </div>
          <div className="stacked-placement-copy">
            <div className="article-stacked-meta stacked-placement-meta">
              <span
                id={sponsorshipId}
                className="content-type card-meta-type partner-placement-label"
                data-promotion-kind="sponsored"
              >
                Sponsored
              </span>
              <span className="stacked-placement-attribution">{attribution || platformName}</span>
            </div>
            <h2 className="stacked-placement-heading">
              <span>{item.name}</span>
            </h2>
            {item.tagline && (
              <p className="article-stacked-description partner-placement-description">
                {item.tagline}
              </p>
            )}
          </div>
          <span className="stacked-placement-visit" aria-hidden="true">
            Visit site <ArrowUpRight size={16} />
          </span>
        </a>
      </article>
    );
  return (
    <article
      className={`partner-placement ${card ? "partner-placement--card" : "partner-placement--compact"}${card && !image ? " partner-placement--branded" : ""} partner-placement--${item.ad_format}`}
      {...stateProps}
    >
      <a {...linkProps} className="partner-placement-link">
        {card ? (
          <>
            {image && (
              <div className="card-image">
                <ArticleImage
                  src={image}
                  variants={item.image_variants}
                  label={item.name}
                  fallback={
                    <span className="partner-placement-hero-logo">
                      <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />
                    </span>
                  }
                />
              </div>
            )}
            <div className="card-copy">
              <div className="partner-placement-heading">
                {!image && <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />}
                <h2>
                  <span>{item.name}</span>
                </h2>
              </div>
              {item.tagline && <p className="partner-placement-description">{item.tagline}</p>}
              <div className="card-meta-row">
                <span className="card-source">
                  <span className="source-avatar" aria-hidden="true">
                    <CatalogIcon
                      url={item.platform?.logo_url ?? null}
                      variants={item.platform?.logo_variants}
                      displaySize={12}
                      source
                    />
                  </span>
                  <span title={platformName}>{platformName}</span>
                </span>
                <span
                  id={sponsorshipId}
                  className="content-type card-meta-type partner-placement-label"
                  data-promotion-kind="sponsored"
                >
                  Sponsored
                </span>
              </div>
              <div className="card-bottom partner-placement-destination">
                {attribution && <span title={attribution}>{attribution}</span>}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="partner-placement-identity">
              <ProductLogo key={item.logo_url} name={item.name} url={item.logo_url} />
              <div>
                <div className="partner-placement-title">
                  <strong>{item.name}</strong>
                  <span
                    id={sponsorshipId}
                    className="content-type card-meta-type partner-placement-label"
                    data-promotion-kind="sponsored"
                  >
                    Sponsored
                  </span>
                </div>
                {item.ad_format === "header_sponsorship" && (item.tagline || attribution) && (
                  <p
                    className="partner-placement-description"
                    title={[attribution, item.tagline].filter(Boolean).join(" · ")}
                  >
                    {[attribution, item.tagline].filter(Boolean).join(" · ")}
                  </p>
                )}
              </div>
            </div>
            {item.ad_format !== "header_sponsorship" && item.tagline && (
              <p className="partner-placement-description">{item.tagline}</p>
            )}
            {item.ad_format !== "header_sponsorship" && attribution && (
              <span className="partner-placement-destination">
                <span title={attribution}>{attribution}</span>
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
  search,
  allow_sponsored_fallback = false,
  ad_formats,
  topic_id,
  maxItems = 2,
  layout,
  requestQueue,
  active,
  children,
  beforeDisplay,
  afterDisplay,
  onDelivery,
}: Props & { active: boolean }) {
  const { user, loading } = useUser();
  const identity = user?.user_id ?? "anonymous";
  const context = JSON.stringify({
    query: {
      surface,
      article_id,
      article_ids,
      search,
      ...(surface === "feed" || surface === "search" ? { allow_sponsored_fallback } : {}),
      ad_formats,
      topic_id,
    } satisfies PlacementQuery,
    identity,
    csrf: user?.csrf_token,
  });
  const [result, setResult] = useState<Delivery | null>(null);
  const [requestedContext, setRequestedContext] = useState<string | null>(null);
  // Once activated, this context can finish loading while an article covers the feed.
  if (active && !loading && maxItems > 0 && requestedContext !== context)
    setRequestedContext(context);
  const cached = useRef<Delivery | null>(null);
  const reserved = useRef(new Set<string>());
  const activity = useRef(active && !loading);
  useLayoutEffect(() => {
    activity.current = active && !loading;
  }, [active, loading]);
  const release = useCallback(() => {
    for (const id of reserved.current) displayed.delete(id);
    reserved.current.clear();
  }, []);
  const publish = useCallback(
    (next: Delivery) => {
      beforeDisplay?.();
      onDelivery?.(next.expired ? [] : next.items);
      cached.current = next;
      setResult(next);
    },
    [beforeDisplay, onDelivery],
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
    if (requestedContext !== context || loading || maxItems < 1) return;
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
        else if (activity.current) reserve(cached.current.items.slice(0, maxItems));
      }
      return release;
    }
    const controller = new AbortController();
    const { query: selection } = JSON.parse(context) as { query: PlacementQuery };
    const query: PlacementQuery = {
      ...selection,
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
    };
    const personalized = identity !== "anonymous";
    const request = (signal: AbortSignal) => {
      // A queued placement gets its full delivery deadline after admission.
      const options = { ...init, signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]) };
      return personalized
        ? userRequest<unknown>("partner-placements/query", options)
        : readerRequest("/api/v1/partner-placements/query", {
            ...options,
            credentials: "omit",
          }).then(async (response) => {
            if (!response.ok) throw new Error("Placements unavailable");
            return response.json() as Promise<unknown>;
          });
    };
    const load = requestQueue
      ? requestQueue.run(request, controller.signal)
      : request(controller.signal);
    void load
      .then((value) => {
        if (controller.signal.aborted) return;
        const loadedAt = Date.now();
        const candidates = parsePartnerPlacements(value);
        const requested = selection.ad_formats ?? ["feed"];
        const eligible = candidates.filter(
          (item) =>
            (!activity.current || !displayed.has(item.product_id)) &&
            partnerReceiptExpiresAt(item.receipt) > loadedAt &&
            requested.includes(item.ad_format) &&
            (item.ad_format !== "header_sponsorship" || selection.surface === "header") &&
            (item.ad_format !== "sponsored_content" ||
              (selection.surface === "feed" &&
                item.article &&
                !selection.article_ids?.includes(item.article.id))),
        );
        const sponsored = maxItems === 1 ? eligible.find((item) => item.sponsored) : undefined;
        const items = (sponsored ? [sponsored] : eligible).slice(0, maxItems);
        if (activity.current) reserve(items);
        publish({
          context,
          owner: identity,
          items,
          tracking: new Map(
            items.map((item) => [
              item.receipt,
              { completed: new Set<string>(), pending: new Set<string>() },
            ]),
          ),
          expiresAt: Math.min(
            loadedAt + PARTNER_RECEIPT_TTL_MS,
            ...items.map((item) => partnerReceiptExpiresAt(item.receipt)),
          ),
          expired: !items.length,
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) onDelivery?.([]);
      });
    return () => {
      controller.abort();
      release();
    };
  }, [
    context,
    requestedContext,
    identity,
    loading,
    csrf,
    maxItems,
    publish,
    release,
    onDelivery,
    requestQueue,
  ]);
  useEffect(() => {
    if (active && !loading && result?.context === context && !result.expired) {
      for (const item of result.items.slice(0, maxItems)) {
        displayed.add(item.product_id);
        reserved.current.add(item.product_id);
      }
    }
    return release;
  }, [active, loading, context, result, maxItems, release]);
  // Keep verified public creative pixels stable while their owner is rechecked.
  // A changed owner, credential, context or expired receipt hides them immediately.
  const visible = result?.context === context && !result.expired;
  const items = result?.owner === identity ? result.items.slice(0, maxItems) : [];
  const cards = items.map((item) => (
    <Placement
      key={item.product_id}
      item={item}
      active={active && !loading && visible}
      visible={visible}
      preserveSpace={!!children}
      layout={layout}
      tracking={result?.tracking.get(item.receipt)}
    />
  ));
  if (children) return children(cards, items, result?.expired ?? true);
  if (!cards.length) return null;
  return (
    <section className="partner-placements" aria-label="Products" hidden={!visible}>
      {cards}
    </section>
  );
}
