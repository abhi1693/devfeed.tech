"use client";

import { useCallback, useLayoutEffect, useRef } from "react";

type Position = { stage: string; left: number; top: number };
const boardAttributes = { id: "data-board-item-id", stage: "data-board-stage" };

/** Animate existing cards moving in the board without changing layout, scrolling or focus. */
export function useBoardMotion(layoutKey = "", attributes = boardAttributes) {
  const region = useRef<HTMLDivElement>(null);
  const previous = useRef(new Map<string, Position>());
  const animations = useRef(new Set<Animation>());
  const preference = useRef<MediaQueryList | null>(null);
  const previousWidth = useRef<number | null>(null);
  const previousLayout = useRef(layoutKey);

  const cancel = useCallback(() => {
    for (const animation of animations.current) animation.cancel();
    animations.current.clear();
  }, []);

  useLayoutEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null;
    preference.current = media;
    function reset() {
      cancel();
      previous.current.clear();
      previousWidth.current = null;
    }
    media?.addEventListener("change", reset);
    window.addEventListener("resize", reset);
    const settings = new MutationObserver(reset);
    settings.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-reduce-motion"],
    });
    return () => {
      media?.removeEventListener("change", reset);
      window.removeEventListener("resize", reset);
      settings.disconnect();
      reset();
    };
  }, [cancel]);

  useLayoutEffect(() => {
    const root = region.current;
    if (!root) return;
    const bounds = root.getBoundingClientRect();
    const next = new Map<string, Position>();
    const moves: { element: HTMLElement; x: number; y: number }[] = [];
    const canAnimate =
      !preference.current?.matches &&
      document.documentElement.dataset.reduceMotion !== "true" &&
      previousLayout.current === layoutKey &&
      previousWidth.current === bounds.width &&
      bounds.width > 0 &&
      bounds.height > 0;

    for (const element of root.querySelectorAll<HTMLElement>(
      `[${attributes.id}][${attributes.stage}]`,
    )) {
      const id = element.getAttribute(attributes.id);
      const stage = element.getAttribute(attributes.stage);
      const rect = element.getBoundingClientRect();
      if (!id || !stage || rect.width <= 0 || rect.height <= 0) continue;
      // Content coordinates remain stable when the board or document scrolls.
      const position = {
        stage,
        left: rect.left - bounds.left + root.scrollLeft,
        top: rect.top - bounds.top + root.scrollTop,
      };
      next.set(id, position);
      const before = previous.current.get(id);
      if (canAnimate && before) {
        const x = before.left - position.left;
        const y = before.top - position.top;
        if (x || y) moves.push({ element, x, y });
      }
    }
    previous.current = next;
    previousWidth.current = bounds.width;
    previousLayout.current = layoutKey;
    for (const { element, x, y } of moves) {
      if (typeof element.animate !== "function") continue;
      const animation = element.animate(
        [
          { transform: `translate(${x}px, ${y}px)`, zIndex: 20 },
          { transform: "translate(0, 0)", zIndex: 20 },
        ],
        { duration: 360, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
      );
      animations.current.add(animation);
      animation.onfinish = animation.oncancel = () => animations.current.delete(animation);
    }
    return cancel;
  });

  return region;
}
