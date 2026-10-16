"use client";
import { useEffect, useLayoutEffect, useRef, useState, useTransition } from "react";
import { useReaderRouter } from "@/lib/reader-navigation";
import { articleDestination, pageDestination } from "@/lib/reader-destination";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { animateReader } from "@/lib/reader-motion";
import { cycleDialogFocus, restoreDialogFocus } from "@/lib/dialog-focus";
import { useArticleNavigation } from "./article-navigation";
import { useReaderSurface } from "./reader-context";
import { readerPromptPolicies, type ArticleIdentity } from "@/lib/reader-context";

export function ArticleModal({
  children,
  direct = false,
  slug,
  canonical,
  article,
  active: visible,
  dismiss: dismissal,
}: {
  children: React.ReactNode;
  direct?: boolean;
  slug?: string;
  canonical?: string;
  article?: ArticleIdentity;
  active?: boolean;
  dismiss?: "feed" | "history";
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const operation = useRef(0);
  const closing = useRef(false);
  const releaseModal = useRef<() => void>(() => {});
  const exitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const exitAnimation = useRef<Animation | null>(null);
  const motionDirection = useRef<-1 | 1 | null>(null);
  const router = useReaderRouter();
  const { motionRef, sequence, overlay } = useArticleNavigation();
  const [pending, startTransition] = useTransition();
  const [loadRequest, setLoadRequest] = useState<{ slug: string; token: number } | null>(null);
  const current = slug ?? overlay?.slug;
  const entry = direct ? "direct" : "intercepted";
  const active = visible ?? (overlay?.entry === entry && overlay.slug === current);
  const dismissTo = dismissal ?? overlay?.dismiss ?? (direct ? "feed" : "history");
  useReaderSurface(
    { currentArticle: article ?? null, promptEligibility: readerPromptPolicies.quiet },
    { active, priority: "foreground" },
  );
  const index = sequence?.slugs.indexOf(current ?? "") ?? -1;
  const previous = index > 0 ? sequence?.slugs[index - 1] : undefined;
  const next = index >= 0 ? sequence?.slugs[index + 1] : undefined;
  const canLoad = index >= 0 && !!sequence?.hasMore;
  const waiting = loadRequest?.slug === current && loadRequest !== null;
  if (loadRequest && loadRequest.slug !== current) setLoadRequest(null);
  const busy = pending || waiting || !!sequence?.loading;
  useLayoutEffect(() => {
    clearTimeout(exitTimer.current);
    exitAnimation.current?.cancel();
    exitAnimation.current = null;
    closing.current = false;
    if (dialog.current) delete dialog.current.dataset.closing;
    return () => {
      // A retained dialog can receive another article while an older page request is pending.
      // This counter invalidates the latest operation; it is not a captured DOM reference.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      operation.current++;
    };
  }, [active, current]);
  function dismiss() {
    if (closing.current) return;
    closing.current = true;
    motionRef.current = null;
    const token = ++operation.current;
    let finished = false;
    const finish = () => {
      if (token !== operation.current || finished) return;
      finished = true;
      clearTimeout(exitTimer.current);
      // Release the browser's modal/inert state before waiting for routing.
      releaseModal.current();
      if (dismissTo === "feed") router.replace(pageDestination("/"));
      else router.back();
    };
    const animation = animateReader(
      dialog.current,
      [
        { opacity: 1, transform: "scale(1)" },
        { opacity: 0, transform: "scale(.98)" },
      ],
      { duration: 160, fill: "forwards" },
    );
    exitAnimation.current = animation;
    if (animation) {
      dialog.current!.dataset.closing = "true";
      void animation.finished.then(finish, finish);
      // A paused or interrupted animation must not retain an invisible modal.
      exitTimer.current = setTimeout(finish, 200);
    } else finish();
  }
  function navigate(target: string) {
    if (motionDirection.current)
      motionRef.current = { target, direction: motionDirection.current, at: Date.now() };
    startTransition(() => router.replace(articleDestination(target)));
  }
  function move(direction: -1 | 1) {
    if (busy || closing.current) return;
    motionDirection.current = direction;
    const target = direction < 0 ? previous : next;
    if (target) navigate(target);
    else if (direction > 0 && canLoad) {
      const token = ++operation.current;
      setLoadRequest({ slug: current!, token });
      void sequence!
        .loadMore()
        .then((target) => {
          if (target && token === operation.current && dialog.current?.open) navigate(target);
        })
        .finally(() => setLoadRequest((request) => (request?.token === token ? null : request)));
    }
  }
  useEffect(() => {
    if (!active || !canonical) return;
    // The retained feed owns Next's metadata during intercepted navigation.
    // Temporarily update its canonical while the article URL is active.
    const existing = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    const link = existing ?? document.createElement("link");
    const previous = existing?.getAttribute("href");
    link.rel = "canonical";
    link.href = canonical;
    if (!existing) document.head.append(link);
    return () => {
      if (link.getAttribute("href") !== canonical) return;
      if (previous !== undefined && previous !== null) link.setAttribute("href", previous);
      else link.remove();
    };
  }, [active, canonical]);
  useEffect(() => {
    if (!active) return;
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      element.close();
      document.body.style.overflow = overflow;
      if (previousFocus) restoreDialogFocus(previousFocus);
    };
    releaseModal.current = release;
    closing.current = false;
    delete element.dataset.closing;
    // Direct entries are already visible in server HTML; upgrade to native modality.
    element.dataset.hydrated = "true";
    if (!element.matches(":modal")) {
      if (element.open) element.close();
      element.showModal();
    }
    const entrance = direct
      ? null
      : animateReader(
          element,
          [
            { opacity: 0, transform: "scale(.98)" },
            { opacity: 1, transform: "scale(1)" },
          ],
          { duration: 200 },
        );
    document.body.style.overflow = "hidden";
    return () => {
      // Invalidate asynchronous page loads and dismissal callbacks, not a DOM ref.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      operation.current++;
      clearTimeout(exitTimer.current);
      entrance?.cancel();
      exitAnimation.current?.cancel();
      release();
    };
  }, [active, direct]);
  useEffect(() => {
    if (!active) return;
    dialog.current?.querySelector(".preview-scroll")?.scrollTo({ top: 0 });
    const intent = motionRef.current;
    if (!intent || intent.target !== current || Date.now() - intent.at > 5000) return;
    const direction = intent.direction;
    motionRef.current = null;
    const animation = animateReader(
      dialog.current?.querySelector(".modal-content") ?? null,
      [
        { opacity: 0, transform: `translateX(${direction * 16}px)` },
        { opacity: 1, transform: "translateX(0)" },
      ],
      { duration: 180 },
    );
    return () => animation?.cancel();
  }, [active, current, motionRef]);
  if (!active) return null;
  return (
    <dialog
      ref={dialog}
      open={direct}
      data-direct-entry={direct || undefined}
      className="article-modal"
      aria-label="Article preview"
      aria-busy={busy}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={(event) => {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === "Tab") {
          if (cycleDialogFocus(event.currentTarget, event.shiftKey)) event.preventDefault();
          return;
        }
        if (event.shiftKey || event.repeat) return;
        const target = event.target as HTMLElement;
        if (
          target.closest(
            'input, textarea, select, [contenteditable="true"], [role="combobox"], [role="listbox"], [role="menu"], [role="slider"]',
          )
        )
          return;
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          move(event.key === "ArrowLeft" ? -1 : 1);
        }
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          dismiss();
      }}
    >
      <header className="modal-toolbar">
        <nav aria-label="Article navigation">
          <button
            type="button"
            aria-label="Previous article"
            aria-keyshortcuts="ArrowLeft"
            title="Previous article (←)"
            disabled={busy || !previous}
            onClick={() => move(-1)}
          >
            <ChevronLeft size={18} aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Next article"
            aria-keyshortcuts="ArrowRight"
            title="Next article (→)"
            disabled={busy || (!next && !canLoad)}
            onClick={() => move(1)}
          >
            <ChevronRight size={18} aria-hidden />
          </button>
        </nav>
        <button
          type="button"
          aria-label="Close preview"
          title="Close preview (Esc)"
          onClick={dismiss}
        >
          <X size={18} aria-hidden />
        </button>
      </header>
      <div className="modal-content">{children}</div>
    </dialog>
  );
}
