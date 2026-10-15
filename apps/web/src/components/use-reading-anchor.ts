"use client";

import { useCallback, useRef } from "react";

/** Keep the article being read in place when optional content settles between results. */
export function useReadingAnchor(selector: string) {
  const ref = useRef<HTMLDivElement>(null);
  const anchor = useRef<{ element: HTMLElement; top: number; scroller: Element } | null>(null);
  const beforeDisplay = useCallback(() => {
    const list = ref.current;
    if (!list) return;
    let scroller: Element = document.scrollingElement ?? document.documentElement;
    for (let parent = list.parentElement; parent; parent = parent.parentElement) {
      if (
        /auto|scroll|overlay/.test(getComputedStyle(parent).overflowY) &&
        parent.scrollHeight > parent.clientHeight
      ) {
        scroller = parent;
        break;
      }
    }
    const viewport =
      scroller === document.scrollingElement
        ? { top: 0, bottom: window.innerHeight }
        : scroller.getBoundingClientRect();
    const visible = (article: HTMLElement) => {
      const rect = article.getBoundingClientRect();
      return rect.bottom > viewport.top && rect.top < viewport.bottom;
    };
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusedArticle = focused?.closest<HTMLElement>(selector);
    const element =
      focused && focusedArticle && list.contains(focusedArticle) && visible(focused)
        ? focused
        : [...list.querySelectorAll<HTMLElement>(selector)].find(visible);
    anchor.current = element
      ? { element, top: element.getBoundingClientRect().top, scroller }
      : null;
  }, [selector]);
  const afterDisplay = useCallback(() => {
    const current = anchor.current;
    anchor.current = null;
    if (!current?.element.isConnected) return;
    const shift = current.element.getBoundingClientRect().top - current.top;
    if (Math.abs(shift) > 1) current.scroller.scrollTop += shift;
  }, []);
  return { ref, beforeDisplay, afterDisplay };
}
