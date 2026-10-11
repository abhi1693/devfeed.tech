"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, X } from "lucide-react";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import {
  parsePartnerPlacements,
  type PartnerPlacement,
  type PlacementQuery,
} from "@/lib/partner-placements";
import {
  hidePartnerProduct,
  placementExclusions,
  recordPlacementView,
} from "@/lib/partner-placement-preferences";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { useUser } from "./user-account";
import { useReaderPromptCoordinator } from "./reader-prompts";

// Simultaneously mounted feed/article contexts must never show the same catalog product twice.
const displayed = new Set<string>();

function Placement({ item, onHide }: { item: PartnerPlacement; onHide: (id: string) => void }) {
  const link = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (!link.current) return;
    return observePartnerPlacement(link.current, item.receipt, () => {
      if (!recordPlacementView(item.product_id, item.frequency_cap)) onHide(item.product_id);
    });
  }, [item, onHide]);
  return (
    <article className="partner-placement" data-product-id={item.product_id}>
      <div className="partner-placement-heading">
        <span className="partner-placement-label">
          {item.sponsored ? "Sponsored" : "Relevant product"}
        </span>
        <button
          type="button"
          className="partner-placement-hide"
          aria-label={`Hide ${item.name}`}
          title="Hide this product for 90 days"
          onClick={() => {
            hidePartnerProduct(item.product_id, item.frequency_cap);
            onHide(item.product_id);
            window.dispatchEvent(
              new CustomEvent("devfeed:partner-product-hidden", { detail: item.product_id }),
            );
          }}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <a
        ref={link}
        href={item.url}
        target="_blank"
        rel={item.sponsored ? "sponsored noopener noreferrer" : "noopener noreferrer"}
        className="partner-placement-link"
        referrerPolicy="no-referrer"
      >
        <strong>{item.name}</strong>
        <ArrowUpRight size={16} aria-hidden="true" />
      </a>
      {item.description && <p>{item.description}</p>}
      <p className="partner-placement-reason">{item.reason}</p>
      <span className="partner-placement-partner">From {item.partner_name}</span>
    </article>
  );
}

/** Optional contextual recommendations load independently of article and feed rendering. */
export function PartnerPlacements(query: PlacementQuery) {
  const prompts = useReaderPromptCoordinator();
  if (
    prompts &&
    (!prompts.ready || (query.surface === "article" ? !prompts.modalOpen : prompts.modalOpen))
  )
    return null;
  // A modal owns the visible reading context. Unmounting the background delivery
  // releases its product reservations before the article requests its candidates.
  return <PlacementDelivery {...query} />;
}

function PlacementDelivery({ surface, article_id, topic_id }: PlacementQuery) {
  const { user, loading, sessionRevision } = useUser();
  const identity =
    surface === "feed" ? (loading ? "loading" : (user?.user_id ?? "anonymous")) : "public";
  const context = JSON.stringify({
    surface,
    article_id,
    topic_id,
    identity,
    sessionRevision: surface === "feed" ? sessionRevision : 0,
  });
  const [result, setResult] = useState<{ context: string; items: PartnerPlacement[] } | null>(null);
  const remove = useCallback(
    (id: string) =>
      setResult(
        (current) =>
          current && { ...current, items: current.items.filter((item) => item.product_id !== id) },
      ),
    [],
  );
  const csrf = surface === "feed" ? user?.csrf_token : undefined;
  useEffect(() => {
    if (identity === "loading") return;
    const exclusions = placementExclusions();
    if (exclusions === null) return;
    const controller = new AbortController();
    const reserved: string[] = [];
    const query: PlacementQuery = {
      surface,
      article_id,
      topic_id,
      exclude_product_ids: [...new Set([...exclusions, ...displayed])].slice(0, 100),
    };
    const init = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(identity !== "public" && identity !== "anonymous" && csrf
          ? { "X-CSRF-Token": csrf }
          : {}),
      },
      body: JSON.stringify(query),
      cache: "no-store" as const,
      referrerPolicy: "no-referrer" as const,
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
    };
    const personalized = surface === "feed" && identity !== "anonymous";
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
        const excluded = placementExclusions(
          new Map(candidates.map((item) => [item.product_id, item.frequency_cap])),
        );
        if (excluded === null) return;
        const items = candidates.filter(
          (item) => !excluded.includes(item.product_id) && !displayed.has(item.product_id),
        );
        for (const item of items) {
          displayed.add(item.product_id);
          reserved.push(item.product_id);
        }
        setResult({ context, items });
      })
      .catch(() => {});
    const hidden = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      setResult((current) =>
        current?.context === context
          ? { ...current, items: current.items.filter((item) => item.product_id !== id) }
          : current,
      );
    };
    const storage = () => {
      const excluded = placementExclusions();
      setResult((current) =>
        current?.context === context
          ? {
              ...current,
              items:
                excluded === null
                  ? []
                  : current.items.filter((item) => !excluded.includes(item.product_id)),
            }
          : current,
      );
    };
    window.addEventListener("devfeed:partner-product-hidden", hidden);
    window.addEventListener("storage", storage);
    return () => {
      controller.abort();
      for (const id of reserved) displayed.delete(id);
      window.removeEventListener("devfeed:partner-product-hidden", hidden);
      window.removeEventListener("storage", storage);
    };
  }, [context, surface, article_id, topic_id, identity, csrf]);
  if (result?.context !== context || !result.items.length) return null;
  return (
    <section className="partner-placements" aria-label="Products relevant to your reading">
      {result.items.map((item) => (
        <Placement key={item.product_id} item={item} onHide={remove} />
      ))}
    </section>
  );
}
